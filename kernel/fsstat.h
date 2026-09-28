// File system usage exported to user space for the df program.
//
// This header is included by BOTH kernel and user code, so the layout
// must be identical in both worlds.  It relies on uint64 being 8 bytes
// on rv64 and must be included after a header that defines uint64
// (kernel/types.h, or user/user.h which pulls it in).
//
// Deliberately not called "struct stat": kernel/stat.h already owns
// that name for per-file metadata.

#ifndef XV6_FSSTAT_H
#define XV6_FSSTAT_H

struct fsstat {
  uint64 blocksize;  // size of a block in bytes (BSIZE)
  uint64 blocks;     // blocks on the device, including metadata ones
  uint64 blocksfree; // blocks whose bitmap bit is clear
  uint64 inodes;     // inode slots on the device, including the unused 0
  uint64 inodesfree; // slots whose dinode type is 0
};

#endif // XV6_FSSTAT_H
