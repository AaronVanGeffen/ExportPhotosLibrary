#!/usr/bin/env python3

import argparse
import os
import shutil
import sqlite3
import sys

from datetime import datetime, timezone
from errno import EEXIST
from signal import signal, SIGINT
from tempfile import mkdtemp

# Closes database and deletes temporary files.
def cleanUp():
    db.close()
    shutil.rmtree(tempDir)
    print("\nDeleted temporary files")

def cleanOnInterrupt(signal, frame):
    cleanUp()
    sys.exit(0)

# Clean up after ourselves in case the script is interrupted.
signal(SIGINT, cleanOnInterrupt)

# Creates a directory if it does not exist.
def ensureDirExists(path):
    if not os.path.isdir(path):
        os.makedirs(path)

# Shows a helpful progress bar.
def showProgressBar(total, completed):
    progress = completed / total * 100
    i = int(progress / 2)
    sys.stdout.write("Progress: [%-50s] %d / %d (%d%%)" % ('=' * i, completed, total, progress))
    sys.stdout.write('\r')
    sys.stdout.flush()

# Command line arguments.
parser = argparse.ArgumentParser(description = 'Exports the contents of a Photos.app library to date-based directories.', formatter_class = argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-s', '--source', default = "~/Pictures/Photos Library.photoslibrary", help = 'path to Photos.app library')
parser.add_argument('-d', '--destination', default = "~/Desktop/Photos", help = 'path to export directory')
parser.add_argument('-n', '--dryrun', default = False, help = "do not copy any files.", action = "store_true")

group = parser.add_mutually_exclusive_group()
group.add_argument('-p', '--progress', default = True, help = "show a bar indicating the completion of the copying progress", action = "store_true")
group.add_argument('-v', '--verbose', default = False, help = "increase the output verbosity", action = "store_true")

args = parser.parse_args()

if args.verbose:
    args.progress = False
if args.progress:
    args.verbose = False

libraryRoot = os.path.expanduser(args.source)
if not os.path.isdir(libraryRoot):
    sys.stderr.write('Library source path does not appear to be a directory.\n')
    sys.exit(-1)

destinationRoot = os.path.expanduser(args.destination)
if not os.path.isdir(destinationRoot):
    sys.stderr.write('Destination path does not appear to be a directory.\n')
    sys.exit(-1)

# Copy the database to a temporary directory, so as to not potentially harm the original.
tempDir = mkdtemp()
databasePathLibrary = os.path.join(tempDir, 'Library.apdb')
shutil.copyfile(os.path.join(libraryRoot, 'Database', 'Library.apdb'), databasePathLibrary)

# Open a connection to this temporary database.
conn = sqlite3.connect(databasePathLibrary)
db = conn.cursor()

# How many images do we have?
db.execute("SELECT COUNT(*) FROM RKMaster WHERE isInTrash = 0 ORDER BY createDate")
numImages = db.fetchone()[0];
print ("Found %d images." % numImages)

# No images?
if numImages == 0:
    sys.exit(0)

# Cocoa/Webkit uses a different epoch rather than the standard UNIX epoch.
epoch = datetime(2001, 1, 1, 0, 0, 0, 0, timezone.utc).timestamp()

index = 0
copied = 0
ignored = 0

# Iterate over them.
for row in db.execute('''
    SELECT m.imagePath, m.fileName, v.imageDate, v.imageTimeZoneOffsetSeconds, v.imageTimeZoneName
    FROM RKMaster AS m
    INNER JOIN RKVersion AS v ON v.masterId = m.modelId
    WHERE m.isInTrash = 0
    ORDER BY v.imageDate'''):
    # Exactly when was this image shot?
    timestamp = datetime.fromtimestamp(epoch + row[2] + row[3], timezone.utc)

    # print ("%-70s %s+%02d00 (%s)" % (row[0], timestamp.strftime("%Y-%m-%d %H:%M:%S"), int(row[3] / 3600), row[4]))
    # continue

    # Figure out where to put the file.
    destinationSubDir = timestamp.strftime("%Y-%m-%d")
    destinationDir = os.path.join(destinationRoot, destinationSubDir)
    destinationFile = os.path.join(destinationDir, row[1])

    # !!! TODO: append location to directory name?

    # Get ready to copy the file.
    sourceImageFile = os.path.join(libraryRoot, "Masters", row[0])
    ensureDirExists(destinationDir)

    # Copy the file if it doesn't exist already.
    if not os.path.isfile(destinationFile):
        if not args.dryrun:
            shutil.copy(sourceImageFile, destinationFile)
        copied += 1
        if args.verbose:
            print ("Copied as %s" % destinationFile)
    else:
        ignored += 1
        if args.verbose:
            print ("Already at destination: %s" % destinationFile)
    
	# !!! TODO: write timestamp to EXIF as needed.

    # !!! TODO: write faces to EXIF comment?

    # Keep track of our progress.
    index += 1
    if args.progress:
        showProgressBar(numImages, index)

cleanUp()

print ("Copying completed.")
print ("%d files copied" % copied)
print ("%d files ignored" % ignored)
