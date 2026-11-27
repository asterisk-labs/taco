#include <taco/taco.h>

int taco_c_header_check(void) {
    taco_read_options options = {0};
    options.pivoted = 1;
    options.location = 1;
    taco_bytes* bytes = 0;
    (void)bytes;
    return TACO_API_VERSION + options.pivoted + options.location;
}
