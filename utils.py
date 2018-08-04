#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

#py 3.5 or 3.6

import re
import html
import shutil

leadingWhitespaceRe = re.compile(r"^\s*$")
trailingWhitepasceRe = re.compile(r"\s*$")

def doClean(text):
    #WWW.java:176
    #Why does the original return null for an empty string?
    if text == None or text == '': return None

    #I think this is equivalent to 179-193
    text = html.unescape(text)

    #Trims whitespace at the beginning and end of lines
    text = re.sub(leadingWhitespaceRe, "", text)
    text = re.sub(trailingWhitepasceRe, "", text)

    return text

def clamp(n, smallest, largest): return max(smallest, min(n, largest))

def status(message, linefeed = False):
    screenSize = shutil.get_terminal_size((60, 20))
    message = message.ljust(screenSize.columns - 1)
    print(message, end='\r\n' if linefeed else '\r')