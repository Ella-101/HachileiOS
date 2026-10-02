// On-disk file system format.
// Both the kernel and user programs use this header file.

#define ROOTINO 1    // root i-number
#define BSIZE   1024 // block size

// Disk layout:
// [ boot block | super block | log | inode blocks |
//                                          free bit map | data blocks]
//
// mkfs computes the super block and builds an initial file system. The
// super block describes the disk layout:
struct superblock {
  uint magic;      // Must be FSMAGIC
  uint size;       // Size of file system image (blocks)
  uint nblocks;    // Number of data blocks
  uint ninodes;    // Number of inodes.
  uint nlog;       // Number of log blocks
  uint logstart;   // Block number of first log block
  uint inodestart; // Block number of first inode block
  uint bmapstart;  // Block number of first free map block
};

// The magic mkfs writes.  It changed when the inode gained mode, uid
// and gid: an image from the old mkfs must be refused rather than read
// with the new field offsets, and the old value is kept only so that
// fsinit() can say which of the two happened.
#define FSMAGIC     0x10203041
#define FSMAGIC_OLD 0x10203040

// Permission bits, in the position they occupy within one class of
// the nine-bit mode: perm_ok() picks a class, shifts it into place,
// and then requires every bit that was asked for.
#define ACC_R 04
#define ACC_W 02
#define ACC_X 01

// Ten direct blocks rather than the usual twelve: the three 16-bit
// fields the inode gained (mode, uid, gid) have to come out of the same
// 64 bytes, because mkfs asserts BSIZE % sizeof(struct dinode) == 0 and
// IPB = 16 depends on it.  The price is 2 KiB off the largest file,
// which MAXFILE absorbs -- it is derived from NDIRECT, and usertests
// uses the symbol rather than the number.
#define NDIRECT   10
#define NINDIRECT (BSIZE / sizeof(uint))
#define MAXFILE   (NDIRECT + NINDIRECT)
#define NLINK_MAX 32767 // nlink is a short; refuse links past its maximum

// On-disk inode structure.  Exactly 64 bytes -- eight 16-bit fields
// (16), size (4) and addrs (44) -- and that is load-bearing: mkfs
// asserts the size divides BSIZE, and IPB is BSIZE / sizeof(*this).
// uid and gid are 16 bits wide, the same range ksetuid() accepts.
struct dinode {
  short type;              // File type
  ushort mode;             // Permission bits (low 9 significant)
  ushort uid;              // Owner
  ushort gid;              // Group
  short major;             // Major device number (T_DEVICE only)
  short minor;             // Minor device number (T_DEVICE only)
  short nlink;             // Number of links to inode in file system
  ushort _pad;             // explicit filler, so no byte is implicit
  uint size;               // Size of file (bytes)
  uint addrs[NDIRECT + 1]; // Data block addresses
};

// Inodes per block.
#define IPB (BSIZE / sizeof(struct dinode))

// Block containing inode i
#define IBLOCK(i, sb) ((i) / IPB + sb.inodestart)

// Bitmap bits per block
#define BPB (BSIZE * 8)

// Block of free map containing bit for block b
#define BBLOCK(b, sb) ((b) / BPB + sb.bmapstart)

// Directory is a file containing a sequence of dirent structures.
#define DIRSIZ 14

// The name field may have DIRSIZ characters and not end in a NUL
// character.
struct dirent {
  ushort inum;
  char name[DIRSIZ] __attribute__((nonstring));
};
