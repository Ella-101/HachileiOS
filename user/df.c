// df: report file system usage, using the fsinfo() system call.
//
// Two views are printed:
//
//   image: every block on the device.  mkfs marks the boot, superblock,
//          log, inode and bitmap blocks as allocated in the same bitmap,
//          so they show up here as used.
//   data : only the blocks that can hold file data, i.e. the image view
//          minus nmeta.
//
// The free count is the same in both views, and that is not a bug: every
// metadata block is marked allocated, so no free block can lie in the
// metadata region.

#include "kernel/types.h"
#include "kernel/fsstat.h"
#include "user/user.h"

int
main(int argc, char *argv[])
{
  struct fsstat st;
  uint64 used, avail, usedb, availb, totalb, pct;
  uint64 dtotal, dused, dusedb;

  if (fsinfo(&st) < 0) {
    fprintf(2, "df: fsinfo failed\n");
    exit(1);
  }
  if (st.blocks == 0) {
    fprintf(2, "df: no file system\n");
    exit(1);
  }
  // nmeta is derived from the superblock, so it can never exceed the
  // device.  A used counter that had drifted below nmeta would make the
  // data-block arithmetic wrap around, so refuse to print it.
  if (st.nmeta > st.blocks || st.blocks - st.blocksfree < st.nmeta) {
    fprintf(2, "df: inconsistent counters (nmeta %ld, blocks %ld)\n", st.nmeta,
            st.blocks);
    exit(1);
  }

  used = st.blocks - st.blocksfree;
  avail = st.blocksfree;
  totalb = st.blocks * st.blocksize;
  usedb = used * st.blocksize;
  availb = avail * st.blocksize;
  pct = used * 100 / st.blocks;

  dtotal = st.blocks - st.nmeta;
  dused = used - st.nmeta;
  dusedb = dused * st.blocksize;

  printf("              total  used  free\n");
  printf("image  blocks  %ld  %ld  %ld\n", st.blocks, used, avail);
  printf("       bytes   %ld  %ld  %ld\n", totalb, usedb, availb);
  printf("data   blocks  %ld  %ld  %ld\n", dtotal, dused, avail);
  printf("       bytes   %ld  %ld  %ld\n", dtotal * st.blocksize, dusedb,
         availb);
  printf("%ld%% of the image is in use; %ld blocks are metadata\n", pct,
         st.nmeta);
  printf("inodes  %ld total, %ld used, %ld free\n", st.inodes,
         st.inodes - st.inodesfree, st.inodesfree);

  exit(0);
}
