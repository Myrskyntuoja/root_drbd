#!/bin/bash

# Shamelessly stolen from https://michael.stapelberg.ch/posts/2012-11-28-root_on_drbd/

which bc >/dev/null 2>&1
if [ ! $? -eq 0 ]; then
    echo "Error: bc is not installed"
    exit 1
fi

if [ $# -lt 1 ]; then
    echo "Error: Please supply block device path"
    echo "Eg. /dev/vg1/backups"
    exit 1
fi

DEVICE=$1

SECTOR_SIZE=$( blockdev --getss $DEVICE )
SECTORS=$( blockdev --getsz $DEVICE )
MD_SIZE=$( echo "((($SECTORS + (2^18)-1) / 262144 * 8) + 72)" | bc )
FS_SIZE=$( echo "$SECTORS - $MD_SIZE" | bc )

MD_SIZE_MB=$( echo "($MD_SIZE / 4 / $SECTOR_SIZE) + 1" | bc )
FS_SIZE_MB=$( echo "($FS_SIZE / 4 / $SECTOR_SIZE)" | bc )

echo "Filesystem: $FS_SIZE_MB MiB"
echo "Filesystem: $FS_SIZE Sectors"
echo "Meta Data:  $MD_SIZE_MB MiB"
echo "Meta Data:  $MD_SIZE Sectors"
