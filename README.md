# root_drbd

 This repo contains some barely tested instructions for migrating your whole
block device containing your root partition to drbd. Having your root FS
encrypted is also possible.
 As for why you would want to do that, it's completely up to you. If you're
reading this, you probably know why you need it.

## Disclaimer

 You can and probably **WILL** break your system in the process. This is not
a walk in the park. If something bad happens, I won't be able to help you,
nor am I actually willing to, you're on your own. Make absolutely sure that
all your data is backed up somewhere else before proceeding.
 You have been warned.
 I've tested this on Debian 12 (MBR) and Mint 22 (GPT). Both of those had DRBD
version 8.4.11. If your DRBD version is 9+, you'll probably want to prohibit
auto-promotion to primary. YMMV.
### DO YOUR OWN RESEARCH!

## Prerequisites

 I'll assume that you have a single block device containing an existing
systemd-based Linux installation. It may or may not have LVM and/or LUKS on it.
It might be MBR or GPT. Whatever. I'll also assume that you actually want to
use drbd for your entire block device. If you only need it for some of your
partitions, you should modify your steps accordingly. Probably you'll want to
try it with some virtual machine first.
 I'll also assume that you do actually have LVM, that your LUKS device is the
only LVM PV, and that your root and swap partitions are LVM logical volumes,
and that your boot partition is separate and (obviously) unencrypted. This is
exactly what the Mint installer does when asked to automatically setup LVM and
encryption.
```
root@user-pc:~# lsblk
NAME                MAJ:MIN RM  SIZE RO TYPE  MOUNTPOINTS
sr0                  11:0    1 1024M  0 rom
vda                 253:0    0   20G  0 disk
├─vda1              253:1    0  512M  0 part  /boot/efi
├─vda2              253:2    0  1,7G  0 part  /boot
└─vda3              253:3    0 17,8G  0 part
  └─vda3_crypt      252:0    0 17,8G  0 crypt
    ├─vgmint-root   252:1    0 15,8G  0 lvm   /
    └─vgmint-swap_1 252:2    0    2G  0 lvm   [SWAP]

root@user-pc:~# gdisk /dev/vda
GPT fdisk (gdisk) version 1.0.10

Partition table scan:
  MBR: protective
  BSD: not present
  APM: not present
  GPT: present

Found valid GPT with protective MBR; using GPT.

Command (? for help): p
Disk /dev/vda: 41943040 sectors, 20.0 GiB
Sector size (logical/physical): 512/512 bytes
Disk identifier (GUID): C0C2648E-F458-4F0E-B3BE-52018AF213BF
Partition table holds up to 128 entries
Main partition table begins at sector 2 and ends at sector 33
First usable sector is 34, last usable sector is 41943006
Partitions will be aligned on 2048-sector boundaries
Total free space is 4029 sectors (2.0 MiB)

Number  Start (sector)    End (sector)  Size       Code  Name
   1            2048         1050623   512.0 MiB   EF00  EFI System Partition
   2         1050624         4550655   1.7 GiB     8300
   3         4550656        41940991   17.8 GiB    8300
```

 I'll also assume that you've checked out the contents of this repo in `/home/user/root_drbd`.

## Measurements

 Use the [size_calc.sh](size_calc.sh) shell script to calculate the size of drbd metadata
for your block device. The script is a modified version of what Michael Stapelberg
came up with in his blog, https://michael.stapelberg.ch/posts/2012-11-28-root_on_drbd/
```
root@user-pc:/home/user/root_drbd# bash size_calc.sh /dev/vda
Filesystem: 20479 MiB
Filesystem: 41941688 Sectors
Meta Data:  1 MiB
Meta Data:  1352 Sectors
```
 Note that if your disk is GPT, it has two copies of the partition table data,
one in the beginning and one in the end. You'll probably want to know the size
of the copy at the end. I've made a python script for that, [gpt_data_size.py](gpt_data_size.py).
```
root@user-pc:/home/user/root_drbd# python3 ./gpt_data_size.py /dev/vda
Logical sector size: 512
Size: 41943040 sectors (21474836480 bytes)
Both primary and alternate headers are OK
Device /dev/vda is a GPT partitioned disk
GUID: c0c2648e-f458-4f0e-b3be-52018af213bf

Alternate header is at LBA 41943039
Alternate partition entries copy starts at LBA 41943007; size: 32 sectors, last LBA: 41943038
Normally total size should be: 33 sectors
Actual adjacent sectors taken: 33, from 41943007 to 41943039
```
 So now we know that the last 33 sectors are used for the alternate GPT header and entries. If you
have an MBR disk instead, there won't be anything at the rear end, so you shouldn't worry about
this step.

 Anyways, knowing that the disk size in sectors is 41943040 and that the DRBD metadata will occupy
1352 sectors, we can figure that the resulting drbd device will have `41943040 - 1352 = 41941688`
sectors, with the last usable sector having an LBA of `41941687`. In case of a GPT disk you have
to subtract another (in my case) 33 sectors from that, so the last usable sector becomes
`41941687 - 33 = 41941654`.

 Now that you have that last sector number, you have to make sure that your last partition ends before
that sector. If you want to be on the safe side (as you should be), leave even more free space at the end.
You will be able to reclaim it afterwards if required. BTW, the LBAs of your original device will match
those of the resulting DRBD, except it will be shorter.

 Resizing partitions, LUKS containers, LVM PVs and filesystems is out of scope of this document.
Please google that. If unsure how to proceed, rethink your decisions once more and make sure you have backups.
I'm lucky to see that the end sector of my `/dev/vda3` is `41940991`, which is far away enough
to my liking from the theoretical limit of `41941654`, so I will proceed skipping the resizing phase.

 From now on, we'll assume that you've dealt with resizing partitions successfully, and your last sectors
are ready to be used for drbd metadata.

## A note on sector size before we proceed

 DRBD will mimic the logical sector size of the device that's used for the storage, BUT if your backends
have different sector sizes, you'll need to be careful. If your secondary is a loop block device, make sure that you create it with the correct sector size. See https://lists.linbit.com/pipermail/drbd-user/2020-November/025743.html

## Getting our hands dirty
### Step 1: prepare DRBD config

 There are plenty resources about DRBD configuration around. You'll probably want to stay away from clusters,
pacemaker, corosync and all the neat stuff that does not fit this scenario. Instead you probably want a stable
primary/secondary replication, and you want it to stay that way until you need to restore from your secondary.

 I'll assume that you have `drbd-utils`, or whatever is the package name in your distribution, installed, and that your
`/etc/drbd.conf` contains something like `include "drbd.d/*.res";` so that you can create a separate `.res` file for
your new resource. You might want to come up with some udev rule to ensure a stable name for your block device; I don't
need it in my case so I'll settle for just `/dev/vda`. For GPT disks you can use the GPT header uuid to identify your
disk; google `ID_PART_TABLE_UUID`. With MBR disks you'll have to be more creative. A custom python/shell script creating
a specific symlink placed into initramfs is always an option.
```
root@user-pc:~# cat /etc/drbd.d/wholedisk.res 
resource wholedisk {
  protocol C;

  startup {
    wfc-timeout 5;
    degr-wfc-timeout 10;
  }

  net {
    cram-hmac-alg sha1;
    shared-secret "DrbdWithExtras";
    after-sb-0pri discard-zero-changes;
    after-sb-1pri discard-secondary;
    after-sb-2pri call-pri-lost-after-sb;
  }

  device /dev/drbd0;

  syncer {
    rate 100M;
    verify-alg sha1;
  }

  on user-pc {
    address 192.168.5.4:7800;
    meta-disk internal;
    disk /dev/vda;
  }

  on drbd-target {
    address 192.168.5.2:7800;
    meta-disk internal;
    disk /dev/loop3;
  }
}
```
### Step 2: make sure your fstab and crypttab use fs/partition labels/uuids or LVM logical volumes and not device names

 That's pretty straightforward, check the contents of `/etc/fstab` and, if LUKS is used, `/etc/crypttab`. Edit them
if necessary. If you did edit them, make sure the new entries work. You should see something like that:
```
root@user-pc:~# cat /etc/fstab
# /etc/fstab: static file system information.
#
# Use 'blkid' to print the universally unique identifier for a
# device; this may be used with UUID= as a more robust way to name devices
# that works even if disks are added and removed. See fstab(5).
#
# <file system> <mount point>   <type>  <options>       <dump>  <pass>
/dev/mapper/vgmint-root /               ext4    errors=remount-ro 0       1
# /boot was on /dev/vda2 during installation
UUID=c0504f42-d6f9-43ed-941f-dd043840ef0d /boot           ext4    defaults        0       2
# /boot/efi was on /dev/vda1 during installation
UUID=D5FF-2E69  /boot/efi       vfat    umask=0077      0       1
/dev/mapper/vgmint-swap_1 none            swap    sw              0       0

root@user-pc:~# cat /etc/crypttab 
vda3_crypt UUID=851792e9-b9d8-4555-a26d-038dd75fdfc0 none luks,discard
```

### Step 3: prepare initramfs hook script

 You need a hook script in order to place the required files into your initramfs. If you use Debian 12 or Mint 22,
you can use [hooks/drbd](hooks/drbd) as is, granted you've installed `kpartx`; otherwise double-check the paths and
file names. The `dummy` module is used to trick DRBD into believing that you have the IP address mentioned in your
resource config. Place the hook file into `/usr/share/initramfs-tools/hooks` (might be different for your distro!),
make it executable. If your distribution uses something radically different to build initramfs images, research it.

### Step 4: prepare initramfs local-top script

 This script is executed during initramfs boot phase before root filesystem is mounted. Also thanks to clever design
of `cryptroot` local-top script's `prereqs` function your script will be executed before trying to setup LUKS. The
task of that script is to set up the drbd resource, and for that it needs to set the correct hostname (i.e. the one
that corresponds to whatever is in your drbd resource file) and the correct IP address, even if it will be totally fake.
For the latter we'll bring up a dummy interface, assign the required IP to it, start the resource, and then remove
the dummy interface.

 I've prepared the [example script](local-top/drbd) but you'll definitely need to edit it. Place the result to
 `/usr/share/initramfs-tools/scripts`, make it executable.

### Step 5: create your new initramfs

 Just run `update-initramfs -u`
```
root@user-pc:~# update-initramfs -u
update-initramfs: Generating /boot/initrd.img-6.8.0-50-generic
I: The initramfs will attempt to resume from /dev/dm-2
I: (/dev/mapper/vgmint-swap_1)
I: Set the RESUME variable to override this.
```

### Step 6: reboot but break into initramfs

 During startup enter the bootloader menu (hold SHIFT with Grub for example) and edit the kernel command line
appending `break=premount` to it, then boot the resulting entry.
```
Spawning shell within the initramfs


BusyBox v1.36.1 (Ubuntu 1:1.36.1-6ubuntu3.1) built-in shell (ash)
Enter 'help' for a list of built-in commands.

(initramfs)
```

### Step 7: a bit dirty but... Initialize your DRBD resource

 We're going to execute our local-top script with `init-resource` parameter to initialize the drbd resource. This
includes forcibly creating DRBD metadata, bringing the resource up and forcibly making it primary.
```
(initramfs) /scripts/local-top/drbd init-resource
md_offset 21474832384
al_offset 21474799616
bm_offset 21474144256

Found some data

 ==> This might destroy existing data! <==

Do you want to proceed?
*** confirmation forced via --force option ***
initializing activity log
initializing bitmap (640 KB) to all zero
Writing meta data...
New drbd meta data block successfully created.
/var/run/drbd: No such file or directory
/var/run/drbd: No such file or directory
(initramfs)
```

### Step 8: repair your GPT table (GPT disks only)

```
(initramfs) gdisk /dev/drbd0
GPT fdisk (gdisk) version 1.0.10

Warning! Disk size is smaller than the main header indicates! Loading
secondary header from the last sector of the disk! You should use 'v' to
verify disk integrity, and perhaps options on the experts' menu to repair
the disk.
Caution: invalid backup GPT header, but valid main header: regenerating
backup header from main header.

Warning! One or more CRCs don't match. You should repair the disk!
Main header: OK
Backup header: ERROR
Main partition table: OK
Backup partition table: ERROR

Partition table scan:
  MBR: protective
  BSD: not present
  APM: not present
  GPT: damaged

****************************************************************************
Caution: Found protective or hybrid MBR and corrupt GPT. Using GPT, but disk
verification and recovery are STRONGLY recommended.
****************************************************************************

Command (? for help): v

Caution: The CRC for the backup partition table is invalid. This table may
be corrupt. This program will automatically create a new backup partition
table when you save your partitions.

Problem: The secondary header's self-pointer indicates that it doesn't reside
at the end of the disk. If you've added a disk to a RAID array, use the 'e'
option on the experts' menu to adjust the secondary header's and partition
table's locations.

Problem: Disk is too small to hold all the data!
(Disk size is 41941688 sectors, needs to be 41943040 sectors.)
The 'e' option on the experts' menu may fix this problem.

Warning: There is a gap between the secondary partition table (ending at sector
41943038) and the secondary metadata (sector 41943039).
This is helpful in some exotic configurations, but is generally ill-advised.
Using 'k' on the experts' menu can adjust this gap.

Problem: GPT claims the disk is larger than it is! (Claimed last usable
sector is 41943006, but backup header is at
41943039 and disk size is 41941688 sectors.
The 'e' option on the experts' menu will probably fix this problem

Partition(s) in the protective MBR are too big for the disk! Creating a
fresh protective or hybrid MBR is recommended.

Identified 5 problems!

Command (? for help): x

Expert command (? for help): e
Relocating backup data structures to the end of the disk

Expert command (? for help): n

Expert command (? for help): w

Final checks complete. About to write GPT data. THIS WILL OVERWRITE EXISTING
PARTITIONS!!

Do you want to proceed? (Y/N): y
OK; writing new GUID partition table (GPT) to /dev/drbd0.
Warning: The kernel is still using the old partition table.
The new table will be used at the next reboot or after you
run partprobe(8) or kpartx(8)
The operation has completed successfully.
(initramfs) gdisk /dev/drbd0
GPT fdisk (gdisk) version 1.0.10

Partition table scan:
  MBR: protective
  BSD: not present
  APM: not present
  GPT: present

Found valid GPT with protective MBR; using GPT.

Command (? for help): v

No problems found. 2677 free sectors (1.3 MiB) available in 2
segments, the largest of which is 2014 (1007.0 KiB) in size.

Command (? for help): q
(initramfs)
```

### Step 9: continue normal boot

```
(initramfs) exit
```

### Step 10: sanity check
```
root@user-pc:~# cat /proc/drbd
version: 8.4.11 (api:1/proto:86-101)
srcversion: 211FB288A383ED945B83420 
 0: cs:StandAlone ro:Primary/Unknown ds:UpToDate/DUnknown   r----s
    ns:0 nr:0 dw:28201 dr:827253 al:8 bm:0 lo:0 pe:0 ua:0 ap:0 ep:1 wo:f oos:20970844
root@user-pc:~# lsblk
NAME                  MAJ:MIN RM  SIZE RO TYPE  MOUNTPOINTS
sr0                    11:0    1 1024M  0 rom   
vda                   253:0    0   20G  0 disk  
├─drbd0               147:0    0   20G  0 disk  
│ ├─drbd0p1           252:0    0  512M  0 part  /boot/efi
│ ├─drbd0p2           252:1    0  1,7G  0 part  /boot
│ └─drbd0p3           252:2    0 17,8G  0 part  
│   └─vda3_crypt      252:3    0 17,8G  0 crypt 
│     ├─vgmint-root   252:4    0 15,8G  0 lvm   /
│     └─vgmint-swap_1 252:5    0    2G  0 lvm   [SWAP]
├─vda1                253:1    0  512M  0 part  
├─vda2                253:2    0  1,7G  0 part  
└─vda3                253:3    0 17,8G  0 part  
```

### Step 11: reboot and check that everything is as intended
```
root@user-pc:~# lsblk
NAME                  MAJ:MIN RM  SIZE RO TYPE  MOUNTPOINTS
sr0                    11:0    1 1024M  0 rom   
vda                   253:0    0   20G  0 disk  
├─drbd0               147:0    0   20G  0 disk  
│ ├─drbd0p1           252:0    0  512M  0 part  /boot/efi
│ ├─drbd0p2           252:1    0  1,7G  0 part  /boot
│ └─drbd0p3           252:2    0 17,8G  0 part  
│   └─vda3_crypt      252:3    0 17,8G  0 crypt 
│     ├─vgmint-root   252:4    0 15,8G  0 lvm   /
│     └─vgmint-swap_1 252:5    0    2G  0 lvm   [SWAP]
├─vda1                253:1    0  512M  0 part  
├─vda2                253:2    0  1,7G  0 part  
└─vda3                253:3    0 17,8G  0 part  
root@user-pc:~# cat /proc/drbd
version: 8.4.11 (api:1/proto:86-101)
srcversion: 211FB288A383ED945B83420 
 0: cs:WFConnection ro:Primary/Unknown ds:UpToDate/DUnknown C r----s
    ns:0 nr:0 dw:19252 dr:489978 al:0 bm:0 lo:0 pe:0 ua:0 ap:0 ep:1 wo:f oos:20970844
```

 Congratulations, your system now works with a DRBD device. Remember to never, ever touch the
underlying block device directly.

## Further steps

 Obviously, bring up a secondary and watch them synchronize. Yay!!!
 