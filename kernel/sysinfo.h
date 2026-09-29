// System-wide information exported to user space for the neofetch
// program.  This header is included by BOTH kernel and user code, so the
// layout must be identical in both worlds.  It relies on uint64 being
// 8 bytes on rv64 and must be included after a header that defines uint64
// (kernel/types.h, or user/user.h which pulls it in).
//
// Unlike struct psinfo there is no page-size constraint here: sysinfo()
// hands back a single struct, not a snapshot of the process table, so
// fields can be appended as new counters appear.

#ifndef XV6_SYSINFO_H
#define XV6_SYSINFO_H

struct sysinfo {
  uint64 ncpu_online;      // harts that have reached scheduler()
  uint64 ncpu_max;         // NCPU, the compile-time cap
  uint64 mem_total;        // physical RAM the kernel manages
  uint64 mem_free;         // freemem() at the same instant
  uint64 pages_total;      // physical pages the allocator manages
  uint64 pages_live;       // pages handed out and not yet returned
  uint64 alloc_calls;      // cumulative kalloc() successes since boot
  uint64 disk_reads;       // physical block reads at virtio_disk_rw()
  uint64 disk_writes;      // physical block writes at virtio_disk_rw()
  uint64 bcache_hits;      // bget() found the block already cached
  uint64 bcache_misses;    // bget() had to take a fresh slot
  uint64 vmfaults;         // pages mapped on demand by vmfault()
  uint64 pages_shared;     // pages with more than one reference (O(1))
  uint64 pages_shared_ref; // the same quantity, from the O(N) table walk
  uint64 kref_calls;       // references added by a copy-on-write fork
  uint64 cow_faults;       // stores resolved by cowfault()
  uint64 cow_copies;       // the subset of those that had to copy a page
};

#endif // XV6_SYSINFO_H
