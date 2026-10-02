#include "kernel/types.h"
#include "kernel/stat.h"
#include "user/user.h"
#include "kernel/fs.h"
#include "kernel/fcntl.h"

static int longlist;

char *
fmtname(char *path)
{
  static char buf[DIRSIZ + 1];
  char *p;

  // Find first character after last slash.
  for (p = path + strlen(path); p >= path && *p != '/'; p--)
    ;
  p++;

  // Return blank-padded name.
  if (strlen(p) >= DIRSIZ)
    return p;
  memmove(buf, p, strlen(p));
  memset(buf + strlen(p), ' ', DIRSIZ - strlen(p));
  buf[sizeof(buf) - 1] = '\0';
  return buf;
}

// Render the ten-character mode column: the file type, then the nine
// permission bits in three groups of read/write/execute.  The kernel
// keeps the type in ip->type and the bits in ip->mode, so this is the
// one place that puts them back together for a human to read.
char *
modechars(char *buf, struct stat *st)
{
  char *p = buf;
  int i;

  *p++ = st->type == T_DIR ? 'd' : st->type == T_DEVICE ? 'c' : '-';
  for (i = 6; i >= 0; i -= 3) {
    *p++ = (st->mode & (1 << (i + 2))) ? 'r' : '-';
    *p++ = (st->mode & (1 << (i + 1))) ? 'w' : '-';
    *p++ = (st->mode & (1 << i)) ? 'x' : '-';
  }
  *p = 0;
  return buf;
}

// One line per entry.  The short form is what ls has always printed;
// -l adds the mode, the link count and the owner.  Both avoid printf
// width specifiers, which xv6's printf does not implement.
void
lsentry(char *name, struct stat *st)
{
  char m[11];

  if (longlist)
    printf("%s %d %d %d %d %s\n", modechars(m, st), (int)st->nlink,
           (int)st->uid, (int)st->gid, (int)st->size, name);
  else
    printf("%s %d %d %d\n", name, st->type, st->ino, (int)st->size);
}

void
ls(char *path)
{
  char buf[512], *p;
  int fd;
  struct dirent de;
  struct stat st;

  if ((fd = open(path, O_RDONLY)) < 0) {
    fprintf(2, "ls: cannot open %s\n", path);
    return;
  }

  if (fstat(fd, &st) < 0) {
    fprintf(2, "ls: cannot stat %s\n", path);
    close(fd);
    return;
  }

  switch (st.type) {
  case T_DEVICE:
  case T_FILE:
    lsentry(fmtname(path), &st);
    break;

  case T_DIR:
    if (strlen(path) + 1 + DIRSIZ + 1 > sizeof buf) {
      printf("ls: path too long\n");
      break;
    }
    strcpy(buf, path);
    p = buf + strlen(buf);
    *p++ = '/';
    while (read(fd, &de, sizeof(de)) == sizeof(de)) {
      if (de.inum == 0)
        continue;
      memmove(p, de.name, DIRSIZ);
      p[DIRSIZ] = 0;
      if (stat(buf, &st) < 0) {
        printf("ls: cannot stat %s\n", buf);
        continue;
      }
      lsentry(fmtname(buf), &st);
    }
    break;
  }
  close(fd);
}

int
main(int argc, char *argv[])
{
  int i, paths = 0;

  for (i = 1; i < argc; i++) {
    if (strcmp(argv[i], "-l") == 0) {
      longlist = 1;
      continue;
    }
    ls(argv[i]);
    paths++;
  }
  // "ls" and "ls -l" both mean the current directory; a flag is not a path.
  if (paths == 0)
    ls(".");
  exit(0);
}
