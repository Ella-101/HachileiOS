#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/fcntl.h"
#include "user/user.h"

// Stress xv6 logging system by having several processes writing
// concurrently to their own file (e.g., logstress f1 f2 f3 f4).
// Runs until killed. Reuse bounded files instead of growing them forever.

#define BUFSZ 2000

char buf[BUFSZ];

int
main(int argc, char **argv)
{
  int fd, n;
  enum { N = 16 };

  if (argc < 2) {
    fprintf(2, "usage: logstress file ...\n");
    exit(1);
  }

  // Persist every directory entry before starting the crash workload.
  for (int i = 1; i < argc; i++) {
    fd = open(argv[i], O_CREATE | O_RDWR | O_TRUNC);
    if (fd < 0) {
      printf("%s: create %s failed\n", argv[0], argv[i]);
      exit(1);
    }
    close(fd);
  }
  sync();

  for (int i = 1; i < argc; i++) {
    int pid1 = fork();
    if (pid1 < 0) {
      printf("%s: fork failed\n", argv[0]);
      exit(1);
    }
    if (pid1 == 0) {
      memset(buf, '0' + i, sizeof(buf));
      for (;;) {
        fd = open(argv[i], O_RDWR | O_TRUNC);
        if (fd < 0) {
          printf("%s: open %s failed\n", argv[0], argv[i]);
          exit(1);
        }
        for (int j = 0; j < N; j++) {
          if ((n = write(fd, buf, sizeof(buf))) != sizeof(buf)) {
            printf("write failed %d\n", n);
            exit(1);
          }
        }
        close(fd);
      }
    }
  }
  printf("logstress ready\n");
  int xstatus;
  for (int i = 1; i < argc; i++) {
    wait(&xstatus);
    if (xstatus != 0)
      exit(xstatus);
  }
  return 0;
}
