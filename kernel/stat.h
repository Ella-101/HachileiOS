#define T_DIR    1 // Directory
#define T_FILE   2 // File
#define T_DEVICE 3 // Device

struct stat {
  int dev;     // File system's disk device
  uint ino;    // Inode number
  short type;  // Type of file
  short nlink; // Number of links to file
  uint64 size; // Size of file in bytes
  // The three fields below are appended rather than inserted, so the
  // offsets of the fields above do not move for any program that
  // already reads this struct.
  uint mode; // Permission bits (low 9 significant)
  uint uid;  // Owner
  uint gid;  // Group
};
