// chmod: change a file's permission bits.
//
// The mode is written in octal, as it is everywhere else, and only the low
// nine bits are meaningful -- the file type lives in its own inode field.

#include "kernel/types.h"
#include "kernel/stat.h"
#include "user/user.h"

// Parse base 8, rejecting anything that is not an octal digit.
//
// Two reasons for this not being atoi().  It is decimal, which would read
// 0644 as six hundred and forty-four and grant execute permission to
// everyone.  And a lenient parser reads "chmod 8 f" as 0, which silently
// removes every permission bit -- turning a typo into a lockout, with no
// indication that anything went wrong.  An explicit 0 is still a valid
// mode: it is a string of octal digits like any other.
static int
octal(char *s)
{
  int v = 0;

  for (; *s; s++) {
    if (*s < '0' || *s > '7')
      return -1;
    v = v * 8 + (*s - '0');
  }
  return v;
}

int
main(int argc, char *argv[])
{
  int mode, i, bad = 0;

  if (argc < 3) {
    fprintf(2, "usage: chmod mode file...\n");
    fprintf(2, "  mode is octal, for example 644 or 755\n");
    exit(1);
  }

  mode = octal(argv[1]);
  if (mode < 0) {
    fprintf(2, "chmod: %s is not an octal mode\n", argv[1]);
    exit(1);
  }

  for (i = 2; i < argc; i++) {
    if (chmod(argv[i], mode) < 0) {
      fprintf(2, "chmod: cannot change %s\n", argv[i]);
      bad = 1;
    }
  }
  exit(bad);
}
