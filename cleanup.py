import os

import config

#Remove truncated temporary files from downloads interrupted at the end of past runs
for root, _, files in os.walk(config.imageDir):
    for name in files:
        if name[-4:] == '_tmp':
            os.unlink(os.path.join(root, name))
