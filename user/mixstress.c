#include "kernel/types.h"
#include "kernel/fcntl.h"
#include "user/user.h"

#define WORKERS 6
#define ROUNDS  12
#define BYTES   8192
static char original[BYTES], received[BYTES];

static void
check(int ok, char *what)
{
  if (!ok) {
    fprintf(2, "mixstress: FAIL %s\n", what);
    exit(1);
  }
}

static void
worker(int id)
{
  char name[] = "mix0";
  name[3] += id;
  for (int r = 0; r < ROUNDS; r++) {
    int p[2], status, fd;
    memset(original, 'a' + id, BYTES);
    check(pipe(p) == 0, "pipe");
    int pid = fork();
    check(pid >= 0, "fork");
    if (pid == 0) {
      close(p[0]);
      memset(original, 'A' + id, BYTES);
      check(write(p[1], original, BYTES) == BYTES, "pipe write");
      close(p[1]);
      fd = open(name, O_CREATE | O_RDWR | O_TRUNC);
      check(fd >= 0, "create");
      check(write(fd, original, BYTES) == BYTES, "file write");
      close(fd);
      exit(0);
    }
    close(p[1]);
    int n = 0, got;
    while ((got = read(p[0], received + n, BYTES - n)) > 0)
      n += got;
    check(n == BYTES, "pipe length");
    close(p[0]);
    check(wait(&status) == pid && status == 0, "child status");
    for (int i = 0; i < BYTES; i++)
      check(original[i] == 'a' + id && received[i] == 'A' + id,
            "COW isolation/pipe data");
    fd = open(name, O_RDONLY);
    check(fd >= 0, "open");
    check(read(fd, received, BYTES) == BYTES, "file read");
    for (int i = 0; i < BYTES; i++)
      check(received[i] == 'A' + id, "file data");
    close(fd);
    check(unlink(name) == 0, "unlink");
  }
  exit(0);
}

int
main(void)
{
  struct fsstat before, after;
  // Warm up the root directory slots before recording block usage.
  for (int i = 0; i < WORKERS; i++) {
    char name[] = "mix0";
    name[3] += i;
    int fd = open(name, O_CREATE | O_RDWR);
    check(fd >= 0, "warmup");
    close(fd);
  }
  for (int i = 0; i < WORKERS; i++) {
    char name[] = "mix0";
    name[3] += i;
    check(unlink(name) == 0, "warmup unlink");
  }
  check(fsinfo(&before) == 0, "fsinfo");
  for (int i = 0; i < WORKERS; i++) {
    int pid = fork();
    check(pid >= 0, "worker fork");
    if (pid == 0)
      worker(i);
  }
  for (int i = 0; i < WORKERS; i++) {
    int status;
    check(wait(&status) > 0 && status == 0, "worker status");
  }
  check(wait(0) == -1, "unreaped child");
  sync();
  check(fsinfo(&after) == 0, "fsinfo after");
  check(before.blocksfree == after.blocksfree &&
          before.inodesfree == after.inodesfree,
        "filesystem leak");
  printf("mixstress: OK (%d workers, %d rounds)\n", WORKERS, ROUNDS);
  exit(0);
}
