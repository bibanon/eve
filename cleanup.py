import os

import config

#Remove truncated temporary files from downloads interrupted at the end of past runs
def cleanup():
    cleanup(config.imageDir)


def cleanup(path):
    for root, _, files in os.walk(path):
        for name in files:
            if name[-4:] == "_tmp":
                os.unlink(os.path.join(root, name))

if __name__ == "__main__":
    cleanup()
