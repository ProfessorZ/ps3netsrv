#ifndef __ABSTRACTFILE_H__
#define __ABSTRACTFILE_H__

#include <stdlib.h>
#include <stdint.h>
#include "compat.h"

class AbstractFile
{
public:
	virtual ~AbstractFile() {}

	bool ps3Mode;

	virtual int open(const char *path, int flags) = 0;
	virtual int close(void) = 0;
	virtual ssize_t read(void *buf, size_t nbyte) = 0;
	virtual ssize_t write(void *buf, size_t nbyte) = 0;
	virtual int64_t seek(int64_t offset, int whence) = 0;
	virtual int fstat(file_stat_t *fs) = 0;

	// Whether a single call to read() can safely satisfy a large, multi-sector
	// span in one shot. False for backends where a big contiguous read could
	// straddle a boundary that must be handled specially (see File::read()).
	virtual bool supportsBulkRead() const { return true; }
};


#endif
