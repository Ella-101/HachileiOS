#define NPROC       64                // maximum number of processes
#define NCPU        8                 // maximum number of CPUs
#define NOFILE      16                // open files per process
#define NFILE       100               // open files per system
#define NINODE      50                // maximum number of active i-nodes
#define NDEV        10                // maximum major device number
#define ROOTDEV     1                 // device number of file system root disk
#define MAXARG      32                // max exec arguments
#define MAXOPBLOCKS 10                // max # of blocks any FS op writes
#define LOGBLOCKS   (MAXOPBLOCKS * 3) // max data blocks in on-disk log
#define NBUF        (MAXOPBLOCKS * 3) // size of disk block cache
#define FSSIZE      2400              // size of file system in blocks
#define MAXPATH     128               // maximum file path name
#define USERSTACK   1                 // user stack pages

// FSSIZE is a budget, not a free parameter.  mkfs gives 2 + nlog(31) +
// ninodeblocks(13) + nbitmap(1) = 47 blocks to metadata, so FSSIZE = 2400
// leaves 2353 for data.  Those must hold the in-image README, all UPROGS
// binaries, and the big file usertests' `writebig' writes: MAXFILE blocks
// (NDIRECT + NINDIRECT = 268) plus one indirect block = 269.  At FSSIZE = 2000
// the WSL build left 302 free, but CI's own toolchain emitted 70 blocks more of
// binaries, leaving 232, and writebig failed with "balloc: out of blocks" --
// the old margin was 43 blocks, less than one more program.  Nothing else
// scales with FSSIZE: the log follows MAXOPBLOCKS, the buffer cache NBUF, the
// bitmap stays a single block, and fs.img is read from disk, not held in memory.
