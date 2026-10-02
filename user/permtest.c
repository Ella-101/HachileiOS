//
// permtest: does the permission model actually refuse what it should?
//
// Must be run as root.  Two things shape the whole program:
//
//   * Demotion is one way.  There is no saved-uid, so a process that calls
//     setuid() can never take its privilege back, and no single process can
//     both drop privilege and observe that it did.  So the parent makes the
//     fixtures and stays root, and the checks that need an unprivileged
//     caller run in a child that calls setgid() and then setuid() -- that
//     order, because after setuid() the group is no longer changeable.
//
//   * Every check is a negative one.  A permission model that refuses
//     everything passes every test of the form "this must be refused", so
//     the checks that should succeed are here too, marked ALLOW.
//
// The exit status is the number of checks that came out wrong, so that
// `testrun permtest` (and the CI harness behind it) can assert on it.
//
#include "kernel/types.h"
#include "kernel/stat.h"
#include "kernel/fcntl.h"
#include "user/user.h"

#define FIXUID 1001
#define FIXGID 1001
#define DIR    "/permtest-dir"

static int failed = 0;
static int setup = 0;

static void
bad(const char *what)
{
  printf("permtest: FAIL %s\n", what);
  failed++;
}

// A call that reports success as 0 and failure as -1.
static void
want_refused(const char *what, int rc)
{
  if (rc == 0)
    bad(what);
}

static void
want_ok(const char *what, int rc)
{
  if (rc != 0)
    bad(what);
}

// An open() result: -1 is the refusal, anything else is a descriptor that
// has to be closed either way.
static void
want_refused_open(const char *what, int fd)
{
  if (fd >= 0) {
    close(fd);
    bad(what);
  }
}

static void
want_allowed_open(const char *what, int fd)
{
  if (fd < 0)
    bad(what);
  else
    close(fd);
}

static void
setup_failed(const char *what)
{
  printf("permtest: setup: %s failed\n", what);
  setup++;
}

// Try exec in a grandchild.  A successful exec never returns, so the caller
// cannot watch the outcome directly; it has to run the program and look at
// its exit status instead.  `want` is that status: 1 when the exec must be
// refused (the code after a failed exec exits 1), 0 when it must succeed
// (the program copied into place exits 0).
static void
want_exec(const char *what, const char *path, int want)
{
  int pid, status = -1;
  char *argv[] = {"permtest-prog", 0};

  if ((pid = fork()) == 0) {
    exec(path, argv);
    exit(1);
  }
  if (pid < 0 || wait(&status) != pid)
    status = -1;
  if (status != want)
    bad(what);
}

// ---------------------------------------------------------------------------
// Fixtures.  Created by the parent, which is still root.

static void
mkfile(const char *path, int mode)
{
  int fd = open(path, O_CREATE | O_WRONLY);

  if (fd < 0) {
    setup_failed(path);
    return;
  }
  if (write(fd, "x", 1) != 1)
    setup_failed(path);
  close(fd);
  if (chmod(path, mode) < 0)
    setup_failed(path);
}

static void
mkdir_mode(const char *path, int mode)
{
  if (mkdir(path) < 0) {
    setup_failed(path);
    return;
  }
  if (chmod(path, mode) < 0)
    setup_failed(path);
}

// The exec checks need a real program whose mode is ours to choose, and the
// only programs in the image belong to the system.
static void
copyfile(const char *from, const char *to, int mode)
{
  char buf[512];
  int n, in, out;

  if ((in = open(from, O_RDONLY)) < 0) {
    setup_failed(from);
    return;
  }
  if ((out = open(to, O_CREATE | O_WRONLY)) < 0) {
    close(in);
    setup_failed(to);
    return;
  }
  while ((n = read(in, buf, sizeof(buf))) > 0) {
    if (write(out, buf, n) != n) {
      setup_failed(to);
      break;
    }
  }
  close(in);
  close(out);
  if (chmod(to, mode) < 0)
    setup_failed(to);
}

// Remove anything a previous run left behind.  Without this a second run in
// the same boot would stop at mkdir(), which would look like a permission
// fault rather than a stale fixture.  Every unlink is allowed to fail: on the
// first run there is nothing to remove.
static void
cleanup(void)
{
  unlink(DIR "/closed/hidden.txt");
  unlink(DIR "/xonly/inside.txt");
  unlink(DIR "/newfile");
  unlink(DIR "/sub");
  unlink(DIR "/open.txt");
  unlink(DIR "/secret.txt");
  unlink(DIR "/group.txt");
  unlink(DIR "/owner.txt");
  unlink(DIR "/ownodd.txt");
  unlink(DIR "/prog");
  unlink(DIR "/prog-x");
  unlink(DIR "/closed");
  unlink(DIR "/xonly");
  unlink(DIR);
}

static void
fixtures(void)
{
  mkdir_mode(DIR, 0755);
  mkfile(DIR "/open.txt", 0644);
  mkfile(DIR "/secret.txt", 0600);
  mkfile(DIR "/group.txt", 0640);
  mkfile(DIR "/owner.txt", 0600);
  // Owned by the test identity, with a mode whose owner bits say no and
  // whose other bits say yes: this is the one shape that separates "the
  // owner class decides" from "fall through to the next class that allows".
  mkfile(DIR "/ownodd.txt", 0004);
  // A directory that cannot be searched, and one that can be searched but
  // not listed.
  mkdir_mode(DIR "/closed", 0700);
  mkfile(DIR "/closed/hidden.txt", 0644);
  mkdir_mode(DIR "/xonly", 0711);
  mkfile(DIR "/xonly/inside.txt", 0644);
  copyfile("/echo", DIR "/prog", 0644);
  copyfile("/echo", DIR "/prog-x", 0755);

  if (chown(DIR "/group.txt", 0, FIXGID) < 0)
    setup_failed("chown " DIR "/group.txt");
  if (chown(DIR "/owner.txt", FIXUID, FIXGID) < 0)
    setup_failed("chown " DIR "/owner.txt");
  if (chown(DIR "/ownodd.txt", FIXUID, FIXGID) < 0)
    setup_failed("chown " DIR "/ownodd.txt");
}

// ---------------------------------------------------------------------------
// Checks that belong to the unprivileged child (uid FIXUID, gid FIXGID).

static int
checks_as_test_user(void)
{
  int before = failed;

  // `other` may read and may not write.
  want_allowed_open("read " DIR "/open.txt (0644, other r)",
                    open(DIR "/open.txt", O_RDONLY));
  want_refused_open("write " DIR "/open.txt (0644, other has no w)",
                    open(DIR "/open.txt", O_WRONLY));

  // Root's own file, owner-only.
  want_refused_open("read " DIR "/secret.txt (0600, not ours)",
                    open(DIR "/secret.txt", O_RDONLY));

  // The group class: this file is group FIXGID and grants group r only.
  // It is also the reason setgid() is a separate call -- if gid always
  // equalled uid, this class could never grant anything.
  want_allowed_open("read " DIR "/group.txt (0640, our gid)",
                    open(DIR "/group.txt", O_RDONLY));
  want_refused_open("write " DIR "/group.txt (0640)",
                    open(DIR "/group.txt", O_WRONLY));

  // The owner class.
  want_allowed_open("read " DIR "/owner.txt (0600, ours)",
                    open(DIR "/owner.txt", O_RDONLY));
  want_allowed_open("write " DIR "/owner.txt (0600, ours)",
                    open(DIR "/owner.txt", O_WRONLY));

  // No fallback between classes.
  want_refused_open("read " DIR "/ownodd.txt (0004: owner bits decide)",
                    open(DIR "/ownodd.txt", O_RDONLY));

  // Search (x) versus list (r) on a directory.
  want_refused_open("read " DIR "/closed/hidden.txt (0700: cannot search)",
                    open(DIR "/closed/hidden.txt", O_RDONLY));
  want_allowed_open("read " DIR "/xonly/inside.txt (0711: search, no list)",
                    open(DIR "/xonly/inside.txt", O_RDONLY));
  want_refused_open("list " DIR "/xonly (0711: no r on the directory)",
                    open(DIR "/xonly", O_RDONLY));

  // Changing a directory: 0755 grants search, not write.
  want_refused_open("create " DIR "/newfile (0755: no w)",
                    open(DIR "/newfile", O_CREATE | O_WRONLY));
  want_refused("mkdir " DIR "/sub (0755: no w)", mkdir(DIR "/sub"));
  want_refused("unlink " DIR "/open.txt (0755: no w)", unlink(DIR "/open.txt"));
  // O_TRUNC on a read-only open: without the rule that truncating is a
  // write, this one slips through and empties the file.
  want_refused_open("truncate " DIR "/open.txt (0644, O_RDONLY|O_TRUNC)",
                    open(DIR "/open.txt", O_RDONLY | O_TRUNC));

  // Attributes are about identity, not about the read/write bits.
  want_refused("chmod " DIR "/open.txt (we are not the owner)",
               chmod(DIR "/open.txt", 0777));
  want_refused("chown " DIR "/open.txt (only uid 0 may give files away)",
               chown(DIR "/open.txt", FIXUID, FIXGID));

  // exec: neither of these files has an x bit for `other`.
  want_exec("exec " DIR "/prog (0644, no x for other)", DIR "/prog", 1);
  want_exec("exec " DIR "/prog-x (0755)", DIR "/prog-x", 0);

  return failed - before;
}

// ---------------------------------------------------------------------------
// Checks that belong to root.

static int
checks_as_root(void)
{
  int before = failed;

  // root passes the read and write bits...
  want_allowed_open("read " DIR "/secret.txt as root (root bypasses 0600)",
                    open(DIR "/secret.txt", O_RDONLY));
  want_allowed_open("write " DIR "/secret.txt as root",
                    open(DIR "/secret.txt", O_WRONLY));
  want_ok("chmod " DIR "/open.txt as root", chmod(DIR "/open.txt", 0644));

  // ...but not execute.  A file with no x bit in any class is not a program,
  // and running one has to fail, root or not.  This is the one place the
  // bypass stops, so it is worth a check of its own.
  want_exec("exec " DIR "/prog as root (no x bit anywhere)", DIR "/prog", 1);

  return failed - before;
}

int
main(void)
{
  int pid, status = -1, wrong;

  cleanup();
  fixtures();

  if (setup) {
    printf("permtest: %d step(s) of setup failed\n", setup);
    exit(1);
  }

  wrong = checks_as_root();

  // setgid() before setuid(): once the uid is not 0 the group can no longer
  // be changed at all.
  if ((pid = fork()) == 0) {
    if (setgid(FIXGID) < 0 || setuid(FIXUID) < 0) {
      printf("permtest: FAIL the child could not demote\n");
      exit(1);
    }
    if (getuid() != FIXUID || getgid() != FIXGID) {
      printf("permtest: FAIL the child ended up as uid=%d gid=%d\n", getuid(),
             getgid());
      exit(1);
    }
    exit(checks_as_test_user());
  }
  if (pid < 0 || wait(&status) != pid) {
    printf("permtest: FAIL the demoted child did not report\n");
    exit(1);
  }
  wrong += status;

  if (wrong == 0) {
    printf("permtest: OK\n");
    exit(0);
  }
  printf("permtest: %d check(s) came out wrong\n", wrong);
  exit(1);
}
