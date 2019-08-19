# A modern 4chan scraper

Eve is a replacement for [Asagi](https://github.com/eksopl/asagi/) written in Python with Eventlet. It is essentially feature complete, with only some rough edges and poor design choices.

It is dramatically more memory efficient than Asagi; archiving Nyafuu's set of boards requires only 200 MB of RAM, and every board on the site only ~1GB. This could be reduced much further if needed by hashing posts kept in memory.

# Features
* Asagi compatible - Eve was built with Asagi as a reference implementation, so it can pick up right where Asagi left off, no schema changes needed.
* Simple design - if you can read Python above the beginner level, you can understand how Eve works. A reasonably competent Python dev should be able to add a new media storage or database backend in a day or two, assuming they don't run into any problems (so a week, really).
* Backdoor server - connect to the scraper and modify configurations on the fly. Query detailed info about the scrape process. Adjust how closely you follow the API ratelimit or start archiving a new board, all without restarting the scraper.
* Simple config file format
* Multi platform - Developed on Windows and Linux, and probably works anywhere you can get a reasonable Python environment up.

# Installation
Eve is still not tested in large production environments, so you should really contact Phoenix on Bibanon's IRC ([#bibanon on irc.rizon.net](irc://irc.rizon.net/bibanon)) or [Matrix](https://matrix.to/#/#bibanon-chat:matrix.org) channels if you want to use it in anything important.

Note: Python beginners, watch out for weird issues mixing versions of the various tools below. Lots of systems include both Python 2 and 3, and plenty of them will have broken setups where `python` points to Python 3, but `virtualenv` points to Python 2, and other similar issues, and this will cause you weird problems.
1. Install Python 3.6. Older versions may work, but this is untested. If your package manager doesn't include a recent enough version, [pyenv](https://github.com/pyenv/pyenv) can download and compile a local copy that shouldn't interfere with your system Python.
2. Create a virtualenv: `virtualenv venv`
3. Activate it: `source venv/bin/activate`
4. Install the assorted Python modules Eve needs into the virtual environment: `pip install -r requirements.txt`. If you get an error when installing mysqlclient, you probably need to install the MySQL development package with your distro's package manager so mysqlclient can compile some stuff. Installing the command line MySQL client while you're at it would probably come in handy at some point.
5. Edit the config file and put in your DB connection info, media directory, what boards you want to archive, and tweak any of the other settings that look interesting. Don't go below `0.12` or so for the ratelimit or you'll get banned. Follow the API rules and keep it at or above 1 unless you really need to (If you're not sure whether you need to, you probably don't need to).

You should now be able to run `python eve.py` and have it start archiving, and report status to the standard output, showing requests as they happen, as well as a display of current queued tasks. Ctrl-C will stop Eve. To leave Eve running long term, you can [use screen](https://www.digitalocean.com/community/tutorials/how-to-install-and-use-screen-on-an-ubuntu-cloud-server) (or byobu or any such tool).

If you want to start Eve via an init script, it might break because it checks the terminal size to properly format text. Since init scripts tend to capture stdout and redirect it to a log file, I have no idea if this will work. If the output looks funky, just tail the log file.

# Dealing with problems
As previously mentioned, Eve is still relatively new software. You'll probably run into problems when using it, and I want to hear about them. Feature requests like support for uploading media to S3 or whatever are also fine. Submit GitHub issues or contact Phoenix at the IRC or Matrix links above. If you are a developer and feel like fixing things yourself, pull requests are welcome.
