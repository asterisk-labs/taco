#pragma once

// A strict RFC 8259 parser. Like I-JSON (RFC 7493) it refuses a key repeated
// in an object, which JSON readers resolve differently, and a number beyond
// the range of a double. Values keep their raw text so a caller can pass a
// nested object on without serializing it again.

#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace taco::json {

class InvalidJson : public std::runtime_error {
  public:
    using std::runtime_error::runtime_error;
};

enum class Kind { null, boolean, number, string, array, object };

struct Value {
    Kind kind = Kind::null;
    bool boolean = false;
    std::string string;
    std::vector<Value> items;
    std::vector<std::pair<std::string, Value>> members;
    // The value as it appears in the document it was parsed from.
    std::string_view raw;

    [[nodiscard]] bool is_null() const noexcept { return kind == Kind::null; }
    [[nodiscard]] bool is_string() const noexcept { return kind == Kind::string; }
    [[nodiscard]] bool is_array() const noexcept { return kind == Kind::array; }
    [[nodiscard]] bool is_object() const noexcept { return kind == Kind::object; }

    // The member named key, or nullptr.
    [[nodiscard]] const Value* find(std::string_view key) const;
};

// Throws InvalidJson. The result points into text, which must outlive it.
Value parse(std::string_view text);

// text as a JSON string literal.
std::string quote(std::string_view text);

} // namespace taco::json
