// df: report file system usage, using the fsinfo() system call.
//
// `total' counts every block in the image, not just the ones that can
// hold file data: mkfs marks the boot, super, log, inode and bitmap
// blocks as allocated in the same bitmap, so they show up here as used.

#include "kernel/types.h"
#include "kernel/fsstat.h"
#include "user/user.h"

int
main(int argc, char *argv[])
{
  struct fsstat st;
  uint64 used, avail, usedb, availb, totalb, pct;

  if (fsinfo(&st) < 0) {
    fprintf(2, "df: fsinfo failed\n");
    exit(1);
  }
  if (st.blocks == 0) {
    fprintf(2, "df: no file system\n");
    exit(1);
  }

  used = st.blocks - st.blocksfree;
  avail = st.blocksfree;
  totalb = st.blocks * st.blocksize;
  usedb = used * st.blocksize;
  availb = avail * st.blocksize;
  pct = used * 100 / st.blocks;

  printf("         total        used        free\n");
  printf("blocks  %ld  %ld  %ld\n", st.blocks, used, avail);
  printf("bytes   %ld  %ld  %ld\n", totalb, usedb, availb);
  printf("%ld%% of the image is in use (%ld-byte blocks)\n", pct,
         st.blocksize);
  printf("inodes  %ld total, %ld used, %ld free\n", st.inodes,
         st.inodes - st.inodesfree, st.inodesfree);

  exit(0);
}
