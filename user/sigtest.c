#include "kernel/types.h"
#include "user/user.h"

static volatile int caught;

static void
handler(int sig)
{
  caught = sig;
  sigreturn();
}

int
main(void)
{
  int child, status;
  uint mask = 1U << SIGTERM;

  if (sigaction(SIGTERM, handler) < 0 || sigmask(mask) < 0 ||
      signal(getpid(), SIGTERM) < 0 || caught != 0 || sigmask(0) < 0 ||
      caught != SIGTERM) {
    fprintf(2, "sigtest: signal mask or sigreturn failed\n");
    exit(1);
  }

  child = fork();
  if (child < 0) {
    fprintf(2, "sigtest: fork failed\n");
    exit(1);
  }
  if (child == 0) {
    pause(10);
    exit(0);
  }
  if (setpgid(child, child) < 0 || killpg(child, SIGSTOP) < 0) {
    fprintf(2, "sigtest: process group signal failed\n");
    exit(1);
  }
  for (int i = 0; i < 1000 && jobstate(child) != 2; i++)
    pause(1);
  if (jobstate(child) != 2 || waitpg(child, &status) != child || status != -2 ||
      killpg(child, SIGCONT) < 0 || wait(&status) != child || status != 0) {
    fprintf(2, "sigtest: stop, continue, or reap failed\n");
    exit(1);
  }

  printf("sigtest: OK\n");
  exit(0);
}
