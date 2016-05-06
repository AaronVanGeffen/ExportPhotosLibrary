#!/usr/bin/env python3

import argparse
import os
import shutil
import sqlite3
import sys

from datetime import datetime, timezone
from errno import EEXIST
from exiftool import ExifTool, fsencode
from signal import signal, SIGINT
from tempfile import mkdtemp

# Closes database and deletes temporary files.
def cleanUp():
    db.close()
    shutil.rmtree(tempDir)
    print("\nDeleted temporary files")

    if 'et' in globals():
        et.terminate()
        print("Closed ExifTool.")

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
parser.add_argument('-e', '--exif', default = True, help = "set EXIF date information in JPEG files.", action = "store_true")
parser.add_argument('-f', '--faces', default = True, help = "set faces information in EXIF comment for JPEG files.", action = "store_true")

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
    sys.stderr.write('Library source path "%s" does not appear to be a directory.\n' % libraryRoot)
    sys.exit(-1)

destinationRoot = os.path.expanduser(args.destination)
if not os.path.isdir(destinationRoot):
    sys.stderr.write('Destination path "%s" does not appear to be a directory.\n' % destinationRoot)
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
numImages = db.fetchone()[0]
print ("Found %d images." % numImages)

# Are we exporting faces?
if args.faces:
    facesDbPath = os.path.join(tempDir, 'Person.db')
    shutil.copyfile(os.path.join(libraryRoot, 'Database', 'apdb', 'Person.db'), facesDbPath)

    fconn = sqlite3.connect(facesDbPath)
    fdb = fconn.cursor()

    fdb.execute("SELECT COUNT(*) FROM RKFace WHERE personId > 0");
    numFaces = fdb.fetchone()[0];
    print ("Found %d tagged faces." % numFaces)

# No images?
if numImages == 0:
    sys.exit(0)

# Cocoa/Webkit uses a different epoch rather than the standard UNIX epoch.
epoch = datetime(2001, 1, 1, 0, 0, 0, 0, timezone.utc).timestamp()

index = 0
copied = 0
ignored = 0

if args.exif:
    et = ExifTool();
    et.start();

# Iterate over them.
for row in db.execute('''
    SELECT m.imagePath, m.fileName, v.imageDate, v.imageTimeZoneOffsetSeconds, v.uuid
    FROM RKMaster AS m
    INNER JOIN RKVersion AS v ON v.masterId = m.modelId
    WHERE m.isInTrash = 0
    ORDER BY v.imageDate'''):
    # Exactly when was this image shot?
    timestamp = datetime.fromtimestamp(epoch + row[2] + row[3], timezone.utc)

    # print ("%-70s %s+%02d00 (%s)" % (row[0], timestamp.strftime("%Y-%m-%d %H:%M:%S"), int(row[3] / 3600)))
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

    # Do we need to set some EXIF data while we're at it?
    if args.exif:
        extension = os.path.splitext(row[1])[1].lower()
        if extension == '.jpg' or extension == '.jpeg':
            currentExif = et.get_tags(("EXIF:DateTimeOriginal", "EXIF:CreateDate"), sourceImageFile if args.dryrun else destinationFile)
            desiredDate = timestamp.strftime("%Y:%m:%d %H:%M:%S")

            # Figure out what the current date in the file is.
            if 'EXIF:CreateDate' in currentExif:
                compareDate = currentExif['EXIF:CreateDate']
            elif 'EXIF:DateTimeOriginal' in currentExif:
                compareDate = currentExif['EXIF:DateTimeOriginal']
            else:
                compareDate = ""

            # Do we need to set a date ourselves?
            if compareDate != desiredDate:
                if args.verbose:
                    print ("> EXIF date '%s' will be replaced with '%s'" % (compareDate, desiredDate))

                cmd = map(fsencode, ['-EXIF:DateTimeOriginal=%s' % desiredDate, '-EXIF:CreateDate=%s' % desiredDate, destinationFile])
                et.execute(*cmd)

    # !!! TODO: write faces to EXIF comment?
    if args.faces:
        fdb.execute('''
            SELECT p.name
            FROM RKPerson AS p
            WHERE p.modelId IN(
                SELECT f.personId
                FROM RKFace AS f
                WHERE f.imageId = ?
            )''', (row[4],))

        faces = fdb.fetchall()
        if len(faces) and args.verbose:
            print ("Faces:", ', '.join([face[0] for face in faces]))

    # Keep track of our progress.
    index += 1
    if args.progress:
        showProgressBar(numImages, index)

print ("Copying completed.")
print ("%d files copied" % copied)
print ("%d files ignored" % ignored)

cleanUp()
