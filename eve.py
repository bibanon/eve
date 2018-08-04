#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

#py 3.5 or 3.6

import json
import shelve
import time
import itertools
import random
import logging
import traceback
import io
import collections
from time import sleep

import cfscrape
import eventlet
import eventlet.db_pool
import eventlet.backdoor
import eventlet.semaphore
import eventlet.event
import erequests
from eventlet.green import MySQLdb

import config
import utils

boards = []

connectionPool = eventlet.db_pool.ConnectionPool(MySQLdb, host='localhost', user='root', passwd='', db='asagi', charset='utf8mb4', max_idle=10, max_size=8)

ratelimitSemaphore = eventlet.semaphore.BoundedSemaphore()
fourChanSession = erequests.session()
cfScraper = cfscrape.create_scraper(sess=fourChanSession)

Request = collections.namedtuple('Request', ['url', 'event'])

insertQuery = ("INSERT INTO `{board}`"
               "  (poster_ip, num, subnum, thread_num, op, timestamp, timestamp_expired, preview_orig, preview_w, preview_h, "
               "  media_filename, media_w, media_h, media_size, media_hash, media_orig, spoiler, deleted, "
               "  capcode, email, name, trip, title, comment, delpass, sticky, locked, poster_hash, poster_country, exif) "
               "    SELECT 0,%s,0,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s "
               "    FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM `{board}` WHERE num = %s AND subnum = 0)"
               "      AND NOT EXISTS (SELECT 1 FROM `{board}_deleted` WHERE num = %s AND subnum = 0);\n")

logger = logging.getLogger(__name__)
logging.basicConfig(filename='eve.log',level=logging.DEBUG)
stderr = logging.StreamHandler()
stderr.setLevel(logging.INFO)
logger.addHandler(stderr)

#https://stackoverflow.com/a/9929970/432690
def add_custom_print_exception():
    old_print_exception = traceback.print_exception
    def custom_print_exception(etype, value, tb, limit=None, file=None):
        tb_output = io.StringIO()
        traceback.print_tb(tb, limit, tb_output)
        logger.error(tb_output.getvalue())
        tb_output.close()
        old_print_exception(etype, value, tb, limit=None, file=None)
    traceback.print_exception = custom_print_exception
add_custom_print_exception()

def ratelimit():
    # print("waiting for ratelimit")
    ratelimitSemaphore.acquire()
    # print('ratelimit acquired')
    eventlet.greenthread.spawn_after(1, ratelimitSemaphore.release)
    return

class Board(object):
    """Holds data related to each board"""

    def __init__(self, board):
        super(Board, self).__init__()
        self.board = board
        self.threads = {}
        self.posts = {}
        self.insertQueue = eventlet.queue.Queue()
        self.insertQuery = insertQuery.format(board = board)
        self.threadUpdateQueue = eventlet.queue.PriorityQueue()

        eventlet.spawn(self.threadListUpdater)
        eventlet.spawn(self.threadUpdateQueuer)
        eventlet.spawn(self.inserter)
    
    def threadListUpdater(self):
        while True:
            evt = eventlet.event.Event()
            scraper.get("https://a.4cdn.org/{}/threads.json".format(self.board), evt)
            threadsJson = evt.wait().json()
            logger.info(self.board + ': fetched threads.json.')
            # logger.debug(self.board + ': done, loading threads.')
            tmp = []
            for page in threadsJson:
                for thread in page['threads']:
                    tmp.append(thread)
            for priority, thread in enumerate(tmp[::-1]):#fetch oldest threads first 
                # print('processing thread {}'.format(thread['no']))
                if thread['no'] not in self.threads:
                    logger.debug("Thread %s is new, queueing", thread['no'])
                    self.threads[thread['no']] = thread
                    self.threadUpdateQueue.put((priority, thread['no']))
                elif thread['last_modified'] != self.threads[thread['no']]['last_modified']: #thread updated
                    if not self.threads[thread['no']].get('update_queued', False):
                        logger.debug("Thread %s is updated, queueing", thread['no'])
                        self.threadUpdateQueue.put((priority, thread['no']))
                        self.threads[thread['no']]['update_queued'] = True
            eventlet.sleep(config.boardUpdateDelay)

    def threadUpdateQueuer(self):
        while True:
            thread = self.threadUpdateQueue.get()[1]#strip off priority
            eventlet.greenthread.spawn_n(self.updateThread, thread)
            self.threadUpdateQueue.task_done()

    def updateThread(self, thread):
        '''Fetch thread and queue changes'''
        evt = eventlet.event.Event()
        scraper.get("https://a.4cdn.org/{}/thread/{}.json".format(self.board, thread), evt)
        r = evt.wait()

        utils.status("fetched {}/{}".format(self.board, thread), linefeed=True)
        if r.status_code != 200:
            print("problem when fetching thread:" + str(r.status_code))
            print(r.text)
            if r.status_code == 404:
                del self.threads[thread]
                return
            elif r.status_code == 400:
                print("HTTP error 400 - what do?")
                # [board for board in boards if board.board == thread[0]] #where did this come from?
                return
            else:
                pass #just break I guess? can't code this if I don't know what would cause it
                logger.error("unexpected code path - figure this out")
        r = r.json()

        utils.status("adding {} {} posts to queue".format(len(r['posts']), self.board), linefeed=True)
        for post in r['posts']:
            post['board'] = self.board
            self.insertQueue.put(post)

        
    
    def inserter(self):
        while True:
            # if insertQueue.empty():
            #     break #only for testing

            post = self.insertQueue.get()
            with connectionPool.item() as conn:
                utils.status("processing post {}:{}, {}qDB {}q4CH".format(post['board'], post['no'], self.insertQueue.qsize(), scraper.requestQueue.qsize()))

                result = conn.cursor().execute(insertQuery.format(board=post['board']),
                    (post['no'], #post number
                     post['resto'] if post['resto'] != 0 else post['no'], #resto is RESponse TO (thread number)
                     0 if post['resto'] != 0 else 1,
                     post.get('time', None),
                     str(post.get('tim')) + "s.jpg" if post.get('tim') else None,
                     post.get('tn_w', 0),
                     post.get('tn_h', 0),
                     post.get('filename', None),
                     post.get('w', 0),
                     post.get('h', 0),
                     post.get('fsize', 0),
                     post.get('md5', None),
                     str(post.get('tim', None)) if post.get('tim') else None,
                     post.get('spoiler', 0),
                     0,
                     post.get('capcode', "N")[0].upper(),
                     None,
                     utils.doClean(post.get('name', 'Anonymous')),
                     post.get('trip', None),
                     utils.doClean(post.get('sub', None)),
                     post.get('com', None),
                     None, #No idea if this is right
                     post.get('sticky', 0),
                     post.get('closed', 0),
                     "Dev" if post.get('id', None) == "Developer" else post.get('id', None),
                     post.get('country', None),
                     None, #FIXME: Implement
                     post['no'], #post number
                     post['no'], #post number
                     ))
                conn.commit()
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
        while True:
            ratelimit()
            request = self.requestQueue.get()
            logger.debug('fetching url %s', request.url)
            request.event.send(cfScraper.get(request.url))
            self.requestQueue.task_done()



if config.boardUpdateDelay < len(config.boards)*2:
    newDelay = len(config.boards)*2
    logger.warning("boardUpdateDelay is too low for the number of configured boards! Increasing delay to %s", newDelay)
    config.boardUpdateDelay = newDelay

for board in config.boards:
    boards.append(Board(board))
    logger.debug("created Board %s", board)

scraper = Scraper()




eventlet.spawn(eventlet.backdoor.backdoor_server, eventlet.listen(('localhost', 3000)))

while True:
    eventlet.sleep(1) #This busy loop keeps all the threads running - this can't possibly be how I'm supposed to do things!




# fetching a/166435402
# adding 1 a posts to queue
# Traceback (most recent call last):
#   File "C:\Python36\lib\site-packages\eventlet\hubs\hub.py", line 458, in fire_timers
#     timer()
#   File "C:\Python36\lib\site-packages\eventlet\hubs\timer.py", line 58, in __call__
#     cb(*args, **kw)
#   File "C:\Python36\lib\site-packages\eventlet\event.py", line 168, in _do_send
#     waiter.switch(result)
#   File "C:\Python36\lib\site-packages\eventlet\greenthread.py", line 218, in main
#     result = function(*args, **kwargs)
#   File "eve.py", line 143, in inserter
#     post['no'], #post number
#   File "C:\Python36\lib\site-packages\eventlet\tpool.py", line 186, in doit
#     result = proxy_call(self._autowrap, f, *args, **kwargs)
#   File "C:\Python36\lib\site-packages\eventlet\tpool.py", line 144, in proxy_call
#     rv = execute(f, *args, **kwargs)
#   File "C:\Python36\lib\site-packages\eventlet\tpool.py", line 125, in execute
#     six.reraise(c, e, tb)
#   File "C:\Python36\lib\site-packages\eventlet\support\six.py", line 689, in reraise
#     raise value
#   File "C:\Python36\lib\site-packages\eventlet\tpool.py", line 83, in tworker
#     rv = meth(*args, **kwargs)
#   File "C:\Python36\lib\site-packages\MySQLdb\cursors.py", line 250, in execute
#     self.errorhandler(self, exc, value)
#   File "C:\Python36\lib\site-packages\MySQLdb\connections.py", line 50, in defaulterrorhandler
#     raise errorvalue
#   File "C:\Python36\lib\site-packages\MySQLdb\cursors.py", line 247, in execute
#     res = self._query(query)
#   File "C:\Python36\lib\site-packages\MySQLdb\cursors.py", line 411, in _query
#     rowcount = self._do_query(q)
#   File "C:\Python36\lib\site-packages\MySQLdb\cursors.py", line 374, in _do_query
#     db.query(q)
#   File "C:\Python36\lib\site-packages\MySQLdb\connections.py", line 277, in query
#     _mysql.connection.query(self, query)
# _mysql_exceptions.DataError: (1406, "Data too long for column 'title' at row 1")