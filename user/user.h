#define SBRK_ERROR ((char *)-1)

#include "kernel/psinfo.h"
#include "kernel/fsstat.h"
#include "kernel/sysinfo.h"

struct stat;

// system calls
int fork(void);
int exit(int) __attribute__((noreturn));
int wait(int *);
int pipe(int *);
int write(int, const void *, int);
int read(int, void *, int);
int close(int);
int kill(int);
int exec(const char *, char **);
int open(const char *, int);
int mknod(const char *, short, short);
int unlink(const char *);
int fstat(int fd, struct stat *);
int link(const char *, const char *);
int mkdir(const char *);
int chdir(const char *);
int dup(int);
int getpid(void);
char *sys_sbrk(int, int);
int pause(int);
int uptime(void);
int sync(void);
int psinfo(struct psinfo *buf, int max);
uint64 freemem(void);
int klog(char *buf, int max, uint64 *seq, uint64 *lost);
int waitx(int *status, uint64 *utime, uint64 *ktime);
int fsinfo(struct fsstat *st);
int sysinfo(struct sysinfo *info);
int setprio(int pid, int prio);
#define SIGTERM 1
#define SIGINT  2
#define SIGKILL 3
#define SIGSTOP 4
#define SIGTSTP 5
#define SIGCONT 6
#define SIG_DFL ((void (*)(int)) - 1)
#define SIG_IGN ((void (*)(int)) - 2)
int signal(int pid, int sig);
int killpg(int pgid, int sig);
int sigaction(int sig, void (*handler)(int));
int sigmask(int mask);
int sigreturn(void);
int setpgid(int pid, int pgid);
int getpgid(void);
int tcsetpgrp(int pgid);
int waitpg(int pgid, int *status);
int jobstate(int pgid);

// ulib.c
int stat(const char *, struct stat *);
char *strcpy(char *, const char *);
void *memmove(void *, const void *, int);
char *strchr(const char *, char c);
int strcmp(const char *, const char *);
char *gets(char *, int max);
uint strlen(const char *);
void *memset(void *, int, uint);
int atoi(const char *);
int memcmp(const void *, const void *, uint);
void *memcpy(void *, const void *, uint);
char *sbrk(int);
char *sbrklazy(int);

// printf.c
void fprintf(int, const char *, ...) __attribute__((format(printf, 2, 3)));
void printf(const char *, ...) __attribute__((format(printf, 1, 2)));

// umalloc.c
void *malloc(uint);
void free(void *);
