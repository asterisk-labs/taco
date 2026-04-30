#ifndef COZIP_VERSION_H
#define COZIP_VERSION_H
#define COZIP_VERSION_MAJOR 2026
#define COZIP_VERSION_MINOR 4
#define COZIP_VERSION_PATCH 30
#define COZIP_VERSION_STRING "2026.4.30"
#ifdef __cplusplus
extern "C" {
#endif
#include "cozip.h"
COZIP_API const char* cozip_version_string(void);
#ifdef __cplusplus
}
#endif
#endif /* COZIP_VERSION_H */
