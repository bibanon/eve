#Eve config file

boardUpdateDelay = 120
imageDir = "./boards"
ratelimitRate = 1

downloadMedia = False
downloadThumbs = True

#Database conection info
host='localhost'
user='root'
passwd=''
db='eve'

boards = [
         "bant",
         "c",
         "e",
         "n",
         "news",
         "out",
         "p",
         "toy",
         "vip",
         "vp",
         "w",
         "wg",
         "wsr",
         ]

#set white- or blacklists for thread subjects per-board. Threads with subjects matching these strings won't be processed.
filters = {
          'c':('include', ['Waifu', 'cirno', 'touhou']),
          'vg':('exclude', ['general', '/ss13g/', 'Homebrew Gang']),
          }
