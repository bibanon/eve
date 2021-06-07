#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

#py 3.5 or 3.6

import re
import html
import shutil
import logging

logger = logging.getLogger("eve")

leadingWhitespaceRe = re.compile(r"^\s*$")
trailingWhitespaceRe = re.compile(r"\s*$")

boards = None
scraper = None
config = None


#YotsubaAbstract.java:83
#I feel like half this shit isn't even relevant anymore
replacements = [
    # Admin-Mod-Dev quotelinks
    (re.compile("<span class=\"capcodeReplies\"><span style=\"font-size: smaller;\"><span style=\"font-weight: bold;\">(?:Administrator|Moderator|Developer) Repl(?:y|ies):</span>.*?</span><br></span>"), ""),
    # Non-public tags
    (re.compile("\\[(/?(banned|moot|spoiler|code))]"), "[\g<1>:lit]"),
    # Comment too long, also EXIF tag toggle
    (re.compile("<span class=\"abbr\">.*?</span>"), ""),
    # EXIF data
    (re.compile("<table class=\"exif\"[^>]*>.*?</table>"), ""),
    # DRAW data
    (re.compile("<br><br><small><b>Oekaki Post</b>.*?</small>"), ""),
    # Banned/Warned text
    (re.compile("<(?:b|strong) style=\"color:\\s*red;\">(.*?)</(?:b|strong)>"), "[banned]\g<1>[/banned]"),
    # moot text
    (re.compile("<div style=\"padding: 5px;margin-left: \\.5em;border-color: #faa;border: 2px dashed rgba\\(255,0,0,\\.1\\);border-radius: 2px\">(.*?)</div>"), "[moot]\g<1>[/moot]"),
    # fortune text
    (re.compile("<span class=\"fortune\" style=\"color:(.*?)\"><br><br><b>(.*?)</b></span>"), "\n\n[fortune color=\"\g<1>\"]\g<2>[/fortune]"),
    # bold text
    (re.compile("<(?:b|strong)>(.*?)</(?:b|strong)>"), "[b]\g<1>[/b]"),
    # code tags
    (re.compile("<pre[^>]*>"), "[code]"),
    (re.compile("</pre>"), "[/code]"),
    # math tags
    (re.compile("<span class=\"math\">(.*?)</span>"), "[math]\g<1>[/math]"),
    (re.compile("<div class=\"math\">(.*?)</div>"), "[eqn]\g<1>[/eqn]"),
    # > implying I'm quoting someone
    (re.compile("<font class=\"unkfunc\">(.*?)</font>"), "\g<1>"),
    (re.compile("<span class=\"quote\">(.*?)</span>"), "\g<1>"),
    (re.compile("<span class=\"(?:[^\"]*)?deadlink\">(.*?)</span>"), "\g<1>"),
    # Links
    (re.compile("<a[^>]*>(.*?)</a>"), "\g<1>"),
    # old spoilers
    (re.compile("<span class=\"spoiler\"[^>]*>(.*?)</span>"), "[spoiler]\g<1>[/spoiler]"),
    # ShiftJIS
    (re.compile("<span class=\"sjis\">(.*?)</span>"), "[shiftjis]\g<1>[/shiftjis]"),
    # new spoilers
    (re.compile("<s>"), "[spoiler]"),
    (re.compile("</s>"), "[/spoiler]"),
    # new line/wbr
    (re.compile("<br\\s*/?>"), "\n"),
    (re.compile("<wbr>"), ""),
]

def doClean(text):
    #WWW.java:176
    #Why does the original return null for an empty string?
    if text == None or text == '': return None

    #I think this is equivalent to 179-193
    text = html.unescape(text)

    #Trims whitespace at the beginning and end of lines
    text = re.sub(leadingWhitespaceRe, "", text)
    text = re.sub(trailingWhitespaceRe, "", text)

    return text

def doCleanFull(text):
    #YotsubaAbstract.java:83
    #Why does the original return null for an empty string?
    if text == None or text == '': return None

    for expr, replacement in replacements:
        text = re.sub(expr, replacement, text)

    return doClean(text)

def clamp(n, smallest, largest): return max(smallest, min(n, largest))

def setObjects(b, s, c):
    global boards
    global scraper
    global config
    boards = b
    scraper = s
    config = c


def status(message="", linefeed=False):
    showMedia = (getattr(config, "downloadMedia", False) or getattr(config, "downloadThumbs", False))
    tmp = ("{}q4CH {}qDB ".format(scraper.requestQueue.qsize(),
                                 sum([board.insertQueue.qsize() for board in boards])) +
          ("{}qMEDIA ".format(sum([board.mediaFetcher.mediaDLQueue.qsize() for board in boards])) if showMedia else "") +
          message)
    toScreen(tmp, linefeed)

def toScreen(message, linefeed):
    logger.debug(message)
    if getattr(config, "logToStdout", False):
        return
    screenSize = shutil.get_terminal_size((60, 20))
    message = message.ljust(screenSize.columns - 1)
    print(message, end='\r\n' if linefeed else '\r')
