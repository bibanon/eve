#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

#py 3.5 or 3.6

import json
import itertools
import random
import logging
import traceback
import io
import os
import collections
import tempfile
import time
import shutil

import erequests
import cfscrape
import eventlet
import eventlet.db_pool
import eventlet.backdoor
import eventlet.semaphore
import eventlet.event
from eventlet.green import MySQLdb

import config
import utils


boards = []

#concurrency control
connectionPool = eventlet.db_pool.ConnectionPool(MySQLdb, host=config.host, user=config.user, passwd=config.passwd, db=config.db, charset='utf8mb4', sql_mode='ANSI', max_idle=10, max_size=8)
ratelimitSemaphore = eventlet.semaphore.BoundedSemaphore()

#network objects
fourChanSession = erequests.session()
cfScraper = cfscrape.create_scraper(sess=fourChanSession)

#namedtuples
Request = collections.namedtuple('Request', ['url', 'event'])
MediaRow = collections.namedtuple('MediaRow',
    ["media_id",
    "media_hash", #base64 encoded MD5 or something?
    "media", #full size filename?
    "preview_op", #OP preview filename
    "preview_reply", #replay preview filename
    "total", # number of instances?
    "banned"])

#SQL queries
insertQuery = ("INSERT INTO `{board}`"
               "  (poster_ip, num, subnum, thread_num, op, timestamp, timestamp_expired, preview_orig, preview_w, preview_h, "
               "  media_filename, media_w, media_h, media_size, media_hash, media_orig, spoiler, deleted, "
               "  capcode, email, name, trip, title, comment, delpass, sticky, locked, poster_hash, poster_country, exif) "
               "    SELECT 0,%s,0,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s "
               "    FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `{board}` WHERE num = %s AND subnum = 0)"
               "      AND NOT EXISTS (SELECT 1 FROM `{board}_deleted` WHERE num = %s AND subnum = 0);\n")
updateQuery = "UPDATE `{board}` SET comment = %s, deleted = %s, media_filename = COALESCE(%s, media_filename), sticky = (%s OR sticky), locked = (%s or locked) WHERE num = %s AND subnum = %s"
updateDeletedQuery = "UPDATE `{board}` SET deleted = 1, timestamp_expired = %s WHERE num = %s AND subnum = 0"
selectMediaQuery = 'SELECT * FROM `{board}_images` WHERE `media_hash` = %s'
with open('create board.sql') as f:
    createTablesQuery = f.read()

#logger stuff
logger = logging.getLogger(__name__)
logging.basicConfig(filename='eve.log',level=logging.DEBUG,format='%(asctime)s %(levelname)s:%(message)s')
stderr = logging.StreamHandler()
stderr.setLevel(logging.INFO)
logger.addHandler(stderr)

#https://stackoverflow.com/a/9929970/432690
def add_custom_print_exception():
    old_print_exception = traceback.print_exception
    def custom_print_exception(etype, value, tb, limit=None, file=None):
        logger.error(traceback.format_exc())
        old_print_exception(etype, value, tb, limit=None, file=None)
    traceback.print_exception = custom_print_exception
add_custom_print_exception()

def ratelimit():
    ratelimitSemaphore.acquire()
    eventlet.greenthread.spawn_after(config.ratelimitRate, ratelimitSemaphore.release)
    return

class Board(object):
    """Each Board manages polling the site and fetching updated
       threads and queueing DB updates and media downloads."""

    def __init__(self, board):
        super(Board, self).__init__()
        self.board = board
        self.threads = {}
        self.insertQueue = eventlet.queue.Queue()
        self.insertQuery = insertQuery.format(board=board)
        self.updateQuery = updateQuery.format(board=board)
        self.threadUpdateQueue = eventlet.queue.PriorityQueue()
        self.mediaFetcher = MediaFetcher(board)

        #check for board tables and create them if necessary
        with connectionPool.item() as conn:
            c = conn.cursor()
            c.execute("SHOW TABLES LIKE '{board}'".format(board = board))
            if c.rowcount == 0:
                self.createTables()
            elif c.rowcount == 1:
                pass
            else:
                logger.error("Something weird happened when checking to see if the board tables exist!")
                logger.error("board: {} rowcount: {}".format(board, c.rowcount))


        eventlet.spawn(self.threadListUpdater)
        eventlet.spawn(self.threadUpdateQueuer)
        eventlet.spawn(self.inserter)
    
    def createTables(self):
        logger.warning("creating tables for "+self.board)
        with connectionPool.item() as conn:
            c = conn.cursor()
            for statement in createTablesQuery.split('\n\n\n'):
                c.execute(statement.format(board = self.board))
            conn.commit()


    def markDeleted(self, postID):
        logger.debug("post {}/{} deleted".format(self.board, postID))
        with connectionPool.item() as conn:
            c = conn.cursor()
            c.execute(updateDeletedQuery.format(board = self.board), (int(time.time()), postID))
            conn.commit()


    def threadListUpdater(self):
        logger.debug('threadListUpdater for {} started'.format(self.board))
        while True:
            evt = eventlet.event.Event()
            scraper.get("https://a.4cdn.org/{}/threads.json".format(self.board), evt)
            threadsJson = evt.wait().json()
            utils.status('fetched {}/threads.json'.format(self.board), linefeed=True)
            tmp = []
            for page in threadsJson:
                for thread in page['threads']:
                    tmp.append(thread)
            for priority, thread in enumerate(tmp[::-1]):#fetch oldest threads first 
                if thread['no'] not in self.threads:
                    logger.debug("Thread %s is new, queueing", thread['no'])
                    self.threads[thread['no']] = thread
                    self.threads[thread['no']]['posts'] = {} #used to track seen posts
                    self.threadUpdateQueue.put((priority, thread['no']))
                elif thread['last_modified'] != self.threads[thread['no']]['last_modified']: #thread updated
                    if not self.threads[thread['no']].get('update_queued', False):
                        logger.debug("Thread %s is updated, queueing", thread['no'])
                        self.threadUpdateQueue.put((priority, thread['no']))
                        self.threads[thread['no']]['last_modified'] = thread['last_modified']
                        self.threads[thread['no']]['update_queued'] = True
            #Clear old threads from memory
            newThreads = [x['no'] for x in tmp]
            for thread in self.threads:
                if thread not in newThreads:
                    logger.debug("thread {}/{} archived".format(self.board, thread))
                    eventlet.greenthread.spawn_after(1, self.threads.pop, thread) #can't modify dict while iterating over it - lazy solution

            eventlet.sleep(config.boardUpdateDelay)

    def threadUpdateQueuer(self):
        logger.debug('threadUpdateQueuer for {} started'.format(self.board))
        while True:
            thread = self.threadUpdateQueue.get()[1]#strip off priority
            eventlet.greenthread.spawn_n(self.updateThread, thread)
            self.threadUpdateQueue.task_done()

    def updateThread(self, thread):
        '''Fetch thread and queue changes'''
        while True:
            evt = eventlet.event.Event()
            scraper.get("https://a.4cdn.org/{}/thread/{}.json".format(self.board, thread), evt)
            r = evt.wait()

            if r.status_code == 404:
                utils.status("404'd:  {}/{}".format(self.board, thread), linefeed=True)
                del self.threads[thread]
                return
            else:
                utils.status("fetched {}/{}".format(self.board, thread), linefeed=True)

            try:
                r = r.json()
            except json.decoder.JSONDecodeError:
                continue #4chan/CloudFlare sometimes sends invalid JSON; try again
            break

        self.threads[thread]['update_queued'] = False

        logger.debug("adding {} {} posts to queue".format(len(r['posts']), self.board))
        for post in r['posts']:
            post['board'] = self.board
            oldPost = self.threads[thread]['posts'].get(post['no'])
            if post != oldPost: #post is new or has been modified since we last saw it
                self.threads[thread]['posts'][post['no']] = post
                self.insertQueue.put(post)

        for postID in self.threads[thread]['posts']: #Check for deletions
            if postID not in [post['no'] for post in r['posts']]:
                self.markDeleted(postID)

    def inserter(self):
        logger.debug('self for {} started'.format(self.board))
        while True:
            post = self.insertQueue.get()
            with connectionPool.item() as conn:
                utils.status("processing post {}:{}".format(post['board'], post['no']))

                result = conn.cursor().execute(insertQuery.format(board=post['board']),
                    (post['no'], #post number
                     post['resto'] if post['resto'] != 0 else post['no'], #resto is RESponse TO (thread number)
                     0 if post['resto'] != 0 else 1,
                     post.get('time', None),
                     str(post.get('tim')) + "s.jpg" if post.get('tim') else None,
                     post.get('tn_w', 0),
                     post.get('tn_h', 0),
                     post['filename']+post['ext'] if 'md5' in post else None,
                     post.get('w', 0),
                     post.get('h', 0),
                     post.get('fsize', 0),
                     post.get('md5', None),
                     str(post['tim'])+post['ext'] if 'md5' in post else None,
                     post.get('spoiler', 0),
                     0,
                     post.get('capcode', "N")[0].upper(),
                     None,
                     utils.doClean(post.get('name', 'Anonymous')),
                     post.get('trip', None),
                     utils.doClean(post.get('sub', None)),
                     utils.doCleanFull(post.get('com', None)),
                     None, #No idea if this is right
                     post.get('sticky', 0),
                     post.get('closed', 0),
                     "Dev" if post.get('id', None) == "Developer" else post.get('id', None),
                     post.get('country', None),
                     None, #The setter for this in Asagi is never referenced anywhere, so this should always be null, right?
                     post['no'], #post number
                     post['no'], #post number
                     ))

                result = conn.cursor().execute(updateQuery.format(board=post['board']),
                    (post.get('com', None),
                     0,
                     post.get('filename', None),
                     post.get('sticky', 0),
                     post.get('closed', 0),
                     post['no'], #post number
                     post['resto'] if post['resto'] != 0 else post['no'], #resto is RESponse TO (thread number)
                     ))
                conn.commit()
            if post.get('md5', False) and (getattr(config, "downloadMedia", False) or getattr(config, "downloadThumbs", False)): #queue media download
                self.mediaFetcher.put(post)
            self.insertQueue.task_done()


class Scraper(object):
    """Manages access to the 4chan API. Satisfies requests to
    the API in the order received without violating the ratelimit"""
    def __init__(self):
        super(Scraper, self).__init__()
        self.requestQueue = eventlet.queue.Queue()
        eventlet.spawn(self.fetcher)

    def get(self, url,evt):
        self.requestQueue.put(Request(url,evt))

    def fetcher(self):
        logger.debug('scraper loop started')
        while True:
            ratelimit()
            request = self.requestQueue.get()
            response = self.download(request)
            request.event.send(response)
            self.requestQueue.task_done()

    def download(self, request):
        while True:
            delay = 5
            response = None
            try:
                logger.debug('fetching url %s', request.url)
                response = cfScraper.get(request.url)
                response.raise_for_status()
                return response
            except Exception as e:
                if isinstance(e, erequests.HTTPError):
                    if response.status_code == 404: #let caller handle 404s
                        return response
                #For everything else, log it and try again
                logger.warning('{} while fetching {}, will try again in {} seconds'.format(e.__class__.__name__, request.url, delay))
                logger.warning('exception args: '+repr(e.args)) #not sure how useful this will be
                eventlet.sleep(delay)
                delay = min(delay + 5, 300)
                continue


class MediaFetcher(object):
    """Handles media downloads for a single board. Instantiated by each Board.

    doesn't support the old directory structure; does anyone care?"""
    def __init__(self, board):
        super(MediaFetcher, self).__init__()
        self.mediaDLQueue = eventlet.queue.Queue()
        self.selectMediaQuery = selectMediaQuery.format(board = board)
        self.board = board

        eventlet.spawn(self.fetcher)

    def put(self, post):
        self.mediaDLQueue.put(post)

    def fetcher(self):
        while True:
            post = self.mediaDLQueue.get()
            logger.debug('fetching media %s', post['md5'])
            if getattr(config, "downloadMedia", False):
                self.download(post['no'], post['resto'] == 0, False, post['tim'], post['ext'], post['md5'])
            if getattr(config, "downloadThumbs", False):
                self.download(post['no'], post['resto'] == 0, True, post['tim'], post['ext'], post['md5'])
            self.mediaDLQueue.task_done()
            utils.status()

    def download(self, postNum, isOp, isPreview, tim, ext, mediaHash):
        #Local.java:198

        #Get metadata from DB
        with connectionPool.item() as conn:
            c = conn.cursor()
            result = c.execute(self.selectMediaQuery, (mediaHash,))
            assert result == 1
            mediaRow = MediaRow(*c.fetchone())

        if mediaRow.banned:
            logger.info('Skipping download of banned file ', mediaHash)
            return

        #determine filename
        #   Added to DB by insert_image_<board> procedure - triggered by before-ins-<board>
        if isPreview:
            filename = mediaRow.preview_op if isOp else mediaRow.preview_reply
        else:
            filename = mediaRow.media

        #if(filename == null) return;
        if filename == None:
            logger.warning("media download failed to determine destination filename")
            logger.warning("post {} hash {}".format(postNum, mediaHash))
            return

        #make directories
        subdirs = (filename[:4], filename[4:6])
        destinationFolder = "{}/{}/{}/{}".format(config.imageDir+"/"+self.board, "thumb" if isPreview else "images", *subdirs) #FIXME use os.path.join
        os.makedirs(destinationFolder, exist_ok = True) #TODO maybe just skip this and use os.renames at the end?

        #set perms on directories
        #TODO

        #determine final file path, and bail if it already exists
        destinationPath = destinationFolder + os.sep + filename
        if os.path.exists(destinationPath):
            logger.debug('skipping download of already downloaded media')
            logger.debug("post {} hash {}".format(postNum, mediaHash))
            return

        #download the URL into a tempfile
        tmp = tempfile.NamedTemporaryFile(delete = False) #FIXME handle leaks on error
        url = "https://i.4cdn.org/{}/{}{}{}".format(self.board, tim, "s" if isPreview else "", ".jpg" if isPreview else ext)

        while True:
            delay = 5
            try:
                logger.debug('fetching media: post {} hash {}'.format(postNum, mediaHash))
                request = cfScraper.get(url)
                request.raise_for_status()
                break
            except Exception as e:
                if isinstance(e, erequests.HTTPError):
                    if request.status_code == 404: #404s are to be expected, just bail when they happen
                        logger.info("404 when downloading media")
                        logger.info("post {} hash {}".format(postNum, mediaHash))
                        return
                # log everything else and try again
                logger.warning('{} while fetching media post {} hash {}, will try again in {} seconds'.format(e.__class__.__name__, postNum, mediaHash, delay))
                logger.warning('exception args: '+repr(e.args)) #not sure how useful this will be
                eventlet.sleep(delay)
                delay = min(delay + 5, 300)
                continue

        for chunk in request.iter_content(chunk_size=1024*512):
            tmp.write(chunk)
        tmp.close()

        #move the tempfile to the final file path
        shutil.move(tmp.name, destinationPath)

        #set permissions on file path
        #webGroupId is never set in asagi, so should we even do this? Is this even relevant today?
        # os.chmod(destinationPath, 0o644)
        #posix.chown(outputFile.getCanonicalPath(), -1, this.webGroupId);
        logger.debug('downloaded media: {}/{}'.format(self.board, filename))


scraper = Scraper()


if config.boardUpdateDelay < len(config.boards)*2:
    newDelay = len(config.boards)*2
    logger.warning("boardUpdateDelay is too low for the number of configured boards! Increasing delay to %s", newDelay)
    config.boardUpdateDelay = newDelay

for board in config.boards:
    boards.append(Board(board))
    logger.debug("created Board %s", board)

utils.setObjects(boards, scraper, config) #pass these to utils for easy referencing in status code


if __name__ == "__main__":
    eventlet.spawn(eventlet.backdoor.backdoor_server, eventlet.listen(('localhost', 3000)), locals())

while True:
    eventlet.sleep(1) #This busy loop keeps all the threads running - this can't possibly be how I'm supposed to do things!
