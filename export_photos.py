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

# Command line arguments.
parser = argparse.ArgumentParser(description = 'Exports the contents of a Photos.app library to date-based directories.', formatter_class = argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-s', '--source', default = "~/Pictures/Photos Library.photoslibrary", help = 'path to Photos.app library')
parser.add_argument('-d', '--destination', default = "~/Desktop/Photos", help = 'path to export directory')
parser.add_argument('-n', '--dryrun', default = False, help = "do not copy any files.", action = "store_true")
parser.add_argument('-e', '--exif', default = True, help = "set EXIF date information in JPEG files.", action = "store_true")
parser.add_argument('-f', '--faces', default = True, help = "set faces information in EXIF comment for JPEG files.", action = "store_true")
parser.add_argument('-l', '--location', default = True, help = "append location to directory names.", action = "store_true")
parser.add_argument('-r', '--region', default = False, help = "prepend region information to locations.", action = "store_true")

group = parser.add_mutually_exclusive_group()
group.add_argument('-p', '--progress', default = True, help = "show a bar indicating the completion of the copying progress", action = "store_true")
group.add_argument('-v', '--verbose', default = False, help = "increase the output verbosity", action = "store_true")

args = parser.parse_args()

if args.region:
    args.location = True
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

# Determine the database directory, in case we're using a case-sensitive FS. (Older libraries have a capitalised directory name.)
databaseDir = os.path.join(libraryRoot, 'database')
if not os.path.isdir(databaseDir):
    databaseDir = os.path.join(libraryRoot, 'Database')

# Are we dealing with an Aperture, iPhoto, or pre-Sierra style Photos library?
isLegacyLibrary = os.path.isfile(os.path.join(databaseDir, 'Library.apdb'))

# Copy the database to a temporary directory, so as to not potentially harm the original.
tempDir = mkdtemp()
if isLegacyLibrary:
	databasePathLibrary = os.path.join(tempDir, 'Library.apdb')
	shutil.copyfile(os.path.join(databaseDir, 'Library.apdb'), databasePathLibrary)
else:
	databasePathLibrary = os.path.join(tempDir, 'photos.db')
	shutil.copyfile(os.path.join(databaseDir, 'photos.db'), databasePathLibrary)

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

# Open a connection to this temporary database.
conn = sqlite3.connect(databasePathLibrary)
conn.row_factory = sqlite3.Row
db = conn.cursor()

# How many images do we have?
db.execute("SELECT COUNT(*) FROM RKMaster WHERE isInTrash = 0 ORDER BY createDate")
numImages = db.fetchone()[0]
print ("Found %d images." % numImages)

# Are we exporting faces?
if args.faces:
    if isLegacyLibrary:
        facesDbPath = os.path.join(tempDir, 'Person.db')
        shutil.copyfile(os.path.join(databaseDir, 'Person.db'), facesDbPath)

        fconn = sqlite3.connect(facesDbPath)
        fconn.row_factory = sqlite3.Row
        fdb = fconn.cursor()
    else:
        fdb = conn.cursor()

    fdb.execute("SELECT COUNT(*) FROM RKFace WHERE personId > 0")
    numFaces = fdb.fetchone()[0];
    print ("Found %d tagged faces." % numFaces)

# What about places?
if args.location:
    if isLegacyLibrary:
        placesDbPath = os.path.join(tempDir, 'Properties.apdb')
        shutil.copyfile(os.path.join(databaseDir, 'Properties.apdb'), placesDbPath)

        pconn = sqlite3.connect(placesDbPath)
        pconn.row_factory = sqlite3.Row
        pdb = pconn.cursor()
        ldb = conn.cursor()
    else:
        pdb = conn.cursor()
        ldb = conn.cursor()

    pdb.execute("SELECT COUNT(*) FROM RKPlace")
    numPlaces = pdb.fetchone()[0];
    print ("Found %d places." % numPlaces)

# No images?
if numImages == 0:
    sys.exit(0)

if args.exif:
    et = ExifTool();
    et.start();


def placeByModelId(modelId):
    ldb.execute('''
        SELECT placeId
        FROM RKPlaceForVersion
        WHERE versionId = ?''', (modelId,))

    placeIds = ', '.join([str(placeId[0]) for placeId in ldb.fetchall()])
    if len(placeIds):
        pdb.execute('''
            SELECT DISTINCT defaultName AS name
            FROM RKPlace
            WHERE modelId IN(%s)
            ORDER BY area ASC''' % placeIds)

        regional_info = pdb.fetchall()
        if len(regional_info):
            if args.region:
                regional_info.reverse()
                return ', '.join(location["name"] for location in regional_info)
            else:
                return regional_info[0]["name"]

    return ''


def facesByUuid(uuId):
    fdb.execute('''
        SELECT p.name
        FROM RKPerson AS p
        WHERE p.modelId IN(
            SELECT f.personId
            FROM RKFace AS f
            WHERE f.imageId = ?
        )''', (uuId,))

    faces = fdb.fetchall()
    return [f["name"] for f in faces]


def currentDateInExif(fileName):
    currentExif = et.get_tags(("EXIF:DateTimeOriginal", "EXIF:CreateDate"), fileName)
    if 'EXIF:CreateDate' in currentExif:
        return currentExif['EXIF:CreateDate']
    elif 'EXIF:DateTimeOriginal' in currentExif:
        return currentExif['EXIF:DateTimeOriginal']
    else:
        return ""


def setDateInExif(fileName, formattedDate):
    cmd = map(fsencode, ['-EXIF:DateTimeOriginal=%s' % formattedDate, '-EXIF:CreateDate=%s' % formattedDate, '-overwrite_original', fileName])
    et.execute(*cmd)


def setExifKeywords(fileName, keywords):
    cmd = map(fsencode, ['-keywords={0}'.format(word) for word in keywords] + ['-overwrite_original', fileName])
    et.execute(*cmd)


# Cocoa/Webkit uses a different epoch rather than the standard UNIX epoch.
epoch = datetime(2001, 1, 1, 0, 0, 0, 0, timezone.utc).timestamp()


def photoTimestamp(row):
    return datetime.fromtimestamp(epoch + row["date"] + row["offset"], timezone.utc)


# Creates a directory if it does not exist.
def ensureDirExists(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def copyPhoto(row, destinationSubDir):
    # Get ready to copy the file.
    destinationDir = os.path.join(destinationRoot, destinationSubDir)
    destinationFile = os.path.join(destinationDir, row["fileName"])
    sourceImageFile = os.path.join(libraryRoot, "Masters", row["imagePath"])
    if not args.dryrun:
        ensureDirExists(destinationDir)

    # Copy the file if it doesn't exist already.
    if not os.path.isfile(destinationFile):
        if not args.dryrun:
            shutil.copy(sourceImageFile, destinationFile)
        if args.verbose:
            print ("Copied as %s" % destinationFile)
        return (destinationFile, 1)

    else:
        if args.verbose:
            print ("Already at destination: %s" % destinationFile)
        return (destinationFile, 2);


# TODO: merge calls to exiftool.
def postProcessPhoto(fileName, row):
    # Do we need to set some EXIF data while we're at it?
    if args.exif:
        extension = os.path.splitext(row["fileName"])[1].lower()
        if extension == '.jpg' or extension == '.jpeg':
            compareDate = currentDateInExif(fileName)
            desiredDate = photoTimestamp(row).strftime("%Y:%m:%d %H:%M:%S")

            # Do we need to set a date ourselves?
            if compareDate != desiredDate:
                if args.verbose:
                    print ("> EXIF date '%s' will be replaced with '%s'" % (compareDate, desiredDate))

                setDateInExif(destinationFile, desiredDate)

    # Set faces as EXIF keywords.
    if args.faces:
        faces = facesByUuid(row["uuid"])
        if len(faces) and args.verbose:
            print ("> Faces:", ', '.join([face for face in faces]))

        setExifKeywords(fileName, faces)


# Shows a helpful progress bar.
def showProgressBar(total, completed):
    progress = completed / total * 100
    i = int(progress / 2)
    sys.stdout.write("Progress: [%-50s] %d / %d (%d%%)" % ('=' * i, completed, total, progress))
    sys.stdout.write('\r')
    sys.stdout.flush()


index = 0
copied = 0
ignored = 0

stack = []
stack_timestamp = ""
places_freq = dict()

# Iterate over the photos.
for row in db.execute('''
    SELECT m.imagePath, m.fileName, v.imageDate AS date, v.imageTimeZoneOffsetSeconds AS offset, v.uuid, v.modelId
    FROM RKMaster AS m
    INNER JOIN RKVersion AS v ON v.masterId = m.modelId
    WHERE m.isInTrash = 0
    ORDER BY v.imageDate'''):

    # Stack photos as long as their capture date matches.
    timestamp = photoTimestamp(row).strftime("%Y-%m-%d")
    if timestamp == stack_timestamp:
        stack.append(row)

        if args.location:
            place = placeByModelId(row["modelId"])
            if len(place):
                places_freq[place] = places_freq.get(place, 0) + 1
                if args.verbose:
                    print ("Place for photo: %s" % place)
            elif args.verbose:
                print ("No place info")

    # Ah, reached another date?
    else:
        # First, process existing stack, if any.
        if len(stack):
            # Figure out the dominant place for this day.
            place = ''
            if args.location and len(places_freq):
                place = max(places_freq, key = places_freq.get)
                if not len(place):
                    place = ''

            # Destination dir
            destinationSubDir = stack_timestamp + (" " + place if len(place) else "")
            if args.verbose:
                print ("Destination subdir for stack (%d photos): \"%s\"" % (len(stack), destinationSubDir))

            # Copy and process files in the stack.
            for photo in stack:
                # Copy the file if it's not in its destination yet.
                (destinationFile, status) = copyPhoto(photo, destinationSubDir)
                if status == 1:
                    copied += 1
                elif status == 2:
                    ignored += 1

                # Apply post-processing.
                if not args.dryrun:
                    postProcessPhoto(destinationFile, photo)

                # Keep track of our progress.
                index += 1
                if args.progress:
                    showProgressBar(numImages, index)

            if args.verbose:
                print ("")

        # Clear the stack
        stack = [row]
        stack_timestamp = timestamp

        if args.location:
            place = placeByModelId(row["modelId"])
            if len(place):
                places_freq = dict({place: 1})
                if args.verbose:
                    print ("Place for photo: %s" % place)
            else:
                places_freq = dict()
                if args.verbose:
                    print ("No place info")


print ("Copying completed.")
print ("%d files copied" % copied)
print ("%d files ignored" % ignored)

cleanUp()
