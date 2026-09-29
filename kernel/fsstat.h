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
  uint64 nmeta;      // of those, blocks that hold no file data
  uint64 blocksfree; // blocks whose bitmap bit is clear
  uint64 inodes;     // inode slots on the device, including the unused 0
  uint64 inodesfree; // slots whose dinode type is 0
};

// nmeta is derived in the kernel as sb.size - sb.nblocks, which is exactly
// how mkfs splits the image (mkfs/mkfs.c computes nblocks = FSSIZE - nmeta).
// df subtracts it to print a data-block-only view: the boot, super, log,
// inode and bitmap blocks are marked allocated in the same bitmap, so they
// are indistinguishable from file data if you only look at used blocks.

#endif // XV6_FSSTAT_H
