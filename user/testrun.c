// Machine-readable exit status for the host harness (sh does not expose $?).
#include "kernel/types.h"
#include "user/user.h"

int
main(int argc, char **argv)
{
  if (argc < 2)
    exit(1);
  int pid = fork(), status = -1;
  if (pid == 0) {
    exec(argv[1], argv + 1);
    fprintf(2, "testrun: exec failed\n");
    exit(127);
  }
  if (pid < 0 || wait(&status) != pid)
    status = -1;
  printf("TEST RESULT %s %d\n", argv[1], status);
  exit(status != 0);
}
