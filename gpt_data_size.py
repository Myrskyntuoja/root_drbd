import argparse
import os
import fcntl
import struct
import array
import binascii
import uuid


# EFI Specification determines the GPT format
# https://www.intel.com/content/dam/www/public/us/en/zip/efi-110.zip


class BlockDevice:
  _BLKSSZGET = 0x1268

  def __init__(self, fd):
    self._fd = fd
    self._logical_sector_size = self.ioctl_read_uint32(BlockDevice._BLKSSZGET)
    cur_pos = os.lseek(fd, 0, os.SEEK_SET)
    size_bytes = os.lseek(fd, 0, os.SEEK_END)
    os.lseek(fd, cur_pos, os.SEEK_SET)
    self._size_lba = int(size_bytes / self.logical_sector_size)
    self._size_bytes = self.size_lba * self.logical_sector_size

  def ioctl_read_uint32(self, ioctl):
    buf = array.array('b', [0, 0, 0, 0])
    fcntl.ioctl(self._fd, ioctl, buf)
    return struct.unpack('I',buf)[0]

  def seek(self, lba):
    lba_i = int(lba)
    if lba_i < 0 or lba_i >= self.size_lba:
      raise ValueError(f"Invalid LBA value: {lba}; size={self.size_lba}")
    os.lseek(self._fd, lba_i * self.logical_sector_size, os.SEEK_SET)

  def read(self, sectors):
    return os.read(self._fd, sectors * self.logical_sector_size)

  def read_bytes_at_least(self, bytes):
    return self.read(self.bytes_to_sectors(bytes))

  def bytes_to_sectors(self, bytes):
    return int((bytes + self.logical_sector_size - 1) / self.logical_sector_size)

  @property
  def logical_sector_size(self):
    return self._logical_sector_size

  @property
  def size_lba(self):
    return self._size_lba

  @property
  def size_bytes(self):
    return self._size_bytes


class GptHeader:
  MIN_SIZE = 92

  def __init__(self, dev, my_lba):
    self._valid = True
    self._my_lba = my_lba
    dev.seek(my_lba)
    header_data = dev.read(1)
    signature = header_data[:8]
    if signature != b'EFI PART':
      self._invalidate("GPT Header Signature is incorrect")
      return
    header_size = struct.unpack('<I', header_data[12:16])[0]
    if header_size > len(header_data):
      self._invalidate(f"GPT Header size is {header_size} but only {len(header_data)} bytes were read from LBA {my_lba}")
      return
    header_crc32 = struct.unpack('<I', header_data[16:20])[0]
    data_to_calc = header_data[:16] + b'\0\0\0\0' + header_data[20:header_size]
    calculated = binascii.crc32(data_to_calc)
    if header_crc32 != calculated:
      self._invalidate(f"GPT Header CRC32 mismatch ({header_crc32} != {calculated}")
      return
    header_my_lba = struct.unpack('<Q', header_data[24:32])[0]
    if header_my_lba != my_lba:
      self._invalidate(f"GPT Header MyLBA field {header_my_lba} does not correspond its actual LBA {my_lba}")
      return
    self._alternate_lba = struct.unpack('<Q', header_data[32:40])[0]
    if self._alternate_lba >= dev.size_lba:
      self._invalidate(f"Alternate LBA {self._alternate_lba} is out of range, device size is {dev.size_lba}")
      return
    self._first_usable_lba = struct.unpack('<Q', header_data[40:48])[0]
    if self._first_usable_lba >= dev.size_lba:
      self._invalidate(f"First usable LBA {self._first_usable_lba} is out of range, device size is {dev.size_lba}")
      return
    self._last_usable_lba = struct.unpack('<Q', header_data[48:56])[0]
    if self._last_usable_lba >= dev.size_lba:
      self._invalidate(f"Last usable LBA {self._last_usable_lba} is out of range, device size is {dev.size_lba}")
      return
    if self._first_usable_lba >= self._last_usable_lba:
      self._invalidate(f"First usable LBA ({self._first_usable_lba}) is not less than last ({self._last_usable_lba})")
      return
    self._disk_uuid = uuid.UUID(bytes_le=header_data[56:72])
    self._part_entries_lba = struct.unpack('<Q', header_data[72:80])[0]
    if self._part_entries_lba >= dev.size_lba:
      self._invalidate(f"Partition entry LBA ({self._part_entries_lba}) is out of range, device size is {dev.size_lba}")
      return
    self._num_part_entries = struct.unpack('<I', header_data[80:84])[0]
    self._part_entry_size = struct.unpack('<I', header_data[84:88])[0]
    if self._part_entry_size % 8 != 0:
      self._invalidate(f"Partition entry size {self._part_entry_size} is not a multiple of 8")
      return
    partition_entries_size = self._num_part_entries * self._part_entry_size
    self._part_entry_size_sectors = dev.bytes_to_sectors(partition_entries_size)
    last_part_entry_lba = self._part_entries_lba + self._part_entry_size_sectors - 1
    if last_part_entry_lba >= dev.size_lba:
      self._invalidate(f"Partition entries lie out of the device LBA range, entries: {self._part_entries_lba}-{last_part_entry_lba}, device 0-{dev.size_lba - 1}")
      return
    dev.seek(self._part_entries_lba)
    part_entries_data = dev.read_bytes_at_least(partition_entries_size)[:partition_entries_size]
    if len(part_entries_data) != partition_entries_size:
      self._invalidate(f"Failed to read {partition_entries_size} bytes starting from LBA {self._part_entries_lba}")
      return
    part_entries_crc32 = struct.unpack('<I', header_data[88:92])[0]
    calculated = binascii.crc32(part_entries_data)
    if part_entries_crc32 != calculated:
      self._invalidate(f"Partition entries CRC32 mismatch ({part_entries_crc32} != {calculated})")
      return
    self._partition_entries_data = part_entries_data

  def _invalidate(self, reason):
    self._valid = False
    self._error = reason

  @property
  def valid(self):
    return self._valid

  @property
  def lba(self):
    return self._my_lba

  @property
  def error(self):
    return self._error if not self._valid else None

  @property
  def alternate_lba(self):
    return self._alternate_lba if self._valid else None

  @property
  def disk_uuid(self):
    return self._disk_uuid if self._valid else None

  @property
  def partition_entries_lba(self):
    return self._part_entries_lba if self._valid else None

  @property
  def partition_entries_count(self):
    return self._num_part_entries if self._valid else None

  @property
  def partition_entry_size_bytes(self):
    return self._part_entry_size if self._valid else None

  @property
  def partition_entries_sectors(self):
    return self._part_entry_size_sectors if self._valid else None

  @property
  def partition_entries_data(self):
    return self._partition_entries_data if self._valid else None


class Gpt:
  def __init__(self, dev):
    self._working_with = None
    if dev.logical_sector_size < GptHeader.MIN_SIZE:
      print(f"Underlying device block size ({dev.logical_sector_size}) is less than GPT Header ({GptHeader.MIN_SIZE}), not supported")
      return
    self.primary = GptHeader(dev, 1) # Primary GPT resides at LBA 1
    if self.primary.valid:
      self._working_with = self.primary
      if self.primary.alternate_lba != dev.size_lba - 1:
        print(f"WARNING!!! Alternate header LBA {self.primary.alternate_lba} is not the last block of the device ({dev.size_lba - 1})!")
      self.alternate = GptHeader(dev, self.primary.alternate_lba)
      if self.alternate.valid:
        print("Both primary and alternate headers are OK")
        if self.primary.disk_uuid != self.alternate.disk_uuid:
          print(f"WARNING!!! Primary header uuid {self.primary.disk_uuid} does not match alternate header uuid {self.alternate.disk_uuid}")
      else:
        print(f"WARNING!!! Alternate header at {self.primary.alternate_lba} is corrupted: {self.alternate.error}")
    else:
      print(f"Primary GPT Header is corrupted: {self.primary.error}")
      self.alternate = GptHeader(dev, dev.size_lba - 1)
      if self.alternate.valid:
        print(f"WARNING!!! Primary GPT Header is corrupted but the backup at LBA {dev.size_lba - 1} seems to be OK. You need to check what's up!!!")
        self._working_with = self.alternate
      else:
        print(f"Alternate GPT Header is corrupted too: {self.alternate.error}")

  @property
  def valid(self):
    return self._working_with is not None

  @property
  def header(self):
    return self._working_with


def print_gpt_info(devname):
  f = os.open(devname, os.O_RDONLY)
  try:
    dev = BlockDevice(f)
    print(f"Logical sector size: {dev.logical_sector_size}")
    print(f"Size: {dev.size_lba} sectors ({dev.size_bytes} bytes)")
    gpt = Gpt(dev)
    if not gpt.valid:
      print(f"Device {devname} does not seem to be a GPT partitioned disk")
      return
    gpthdr = gpt.header
    print(f"Device {devname} is a GPT partitioned disk")
    print(f"GUID: {gpthdr.disk_uuid}\n")
    if gpt.alternate.valid:
      last_lba = gpt.alternate.partition_entries_lba + gpt.alternate.partition_entries_sectors - 1
      print(f"Alternate header is at LBA {gpt.alternate.lba}")
      print(f"Alternate partition entries copy starts at LBA {gpt.alternate.partition_entries_lba}; "
            f"size: {gpt.alternate.partition_entries_sectors} sectors, "
            f"last LBA: {last_lba}")
      gap = 0
      entries_sec = gpt.alternate.partition_entries_sectors
      first = gpt.alternate.partition_entries_lba
      last = gpt.alternate.lba
      if gpt.alternate.lba < last_lba:
        first = gpt.alternate.lba
        last = last_lba
        print(f"GPT header is written before its partition entries!")
        if gpt.alternate.lba != gpt.alternate.partition_entries_lba - 1:
          gap = gpt.alternate.partition_entries_lba - 1 - gpt.alternate.lba
      elif last_lba != gpt.alternate.lba - 1:
        gap = gpt.alternate_lba - 1 - last_lba
    else:
      print("!!! Speculating based on the primary header !!!")
      gap = 0
      entries_sec = gpt.primary.partition_entries_sectors
      last = dev.size_lba - 1
      first = last - gpt.primary.partition_entries_sectors
    if gap > 0:
      print(f"Partition entries are not adjacent to the GPT header block! There's a gap of {gap} sectors between them")
    print(f"Normally total size should be: {gpthdr.partition_entries_sectors + 1} sectors")
    print(f"Actual adjacent sectors taken: {last - first + 1}, from {first} to {last}")
    if last != dev.size_lba - 1:
      print(f"There's also {dev.size_lba - 1 - last} sectors not used by GPT after the alternative copy, so total size is {dev.size_lba - first} sectors, from {first} to {dev.size_lba - 1}")
  finally:
    os.close(f)


if __name__ == '__main__':
  parser = argparse.ArgumentParser(prog=__file__,
      description='Determine the location and size of GPT alternative partition table')
  parser.add_argument('device')
  args = parser.parse_args()
  print_gpt_info(args.device)
