// id: print the calling process's identity.
//
// Small on purpose, and it is the cheapest way to see that fork and exec
// carry the identity along: run `id`, then a child of `sh` run `id` again,
// and the numbers are the same.

#include "kernel/types.h"
#include "user/user.h"

int
main(void)
{
  printf("uid=%d gid=%d\n", getuid(), getgid());
  exit(0);
}
