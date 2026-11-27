#include "json.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <unordered_set>

namespace taco::json {
namespace {

constexpr int max_depth = 256;

// Whether a number that matches the JSON grammar is within the range of a
// double. strtod takes the radix point from the locale, so it reads the
// digits as an integer with an exponent.
bool fits_double(std::string_view number) {
    const auto mark = number.find_first_of("eE");
    std::int64_t exponent = 0;
    if (mark != std::string_view::npos) {
        std::string_view digits = number.substr(mark + 1);
        const bool negative = digits.front() == '-';
        if (digits.front() == '+' || negative)
            digits.remove_prefix(1);
        // Clamped far beyond the range of a double.
        for (const char c : digits)
            exponent = std::min<std::int64_t>(exponent * 10 + (c - '0'), 1'000'000'000);
        if (negative)
            exponent = -exponent;
    }
    std::string mantissa(number.substr(0, mark));
    if (const auto point = mantissa.find('.'); point != std::string::npos) {
        exponent -= static_cast<std::int64_t>(mantissa.size() - point - 1);
        mantissa.erase(point, 1);
    }
    return std::isfinite(std::strtod((mantissa + "e" + std::to_string(exponent)).c_str(), nullptr));
}

class Parser {
  public:
    explicit Parser(std::string_view text) : text_(text) {}

    Value document() {
        skip_space();
        Value value = parse_value(0);
        skip_space();
        if (position_ != text_.size())
            invalid("unexpected data after the document");
        return value;
    }

  private:
    [[noreturn]] void invalid(const std::string& reason) const {
        throw InvalidJson(reason + " at byte " + std::to_string(position_));
    }

    [[nodiscard]] bool at_end() const noexcept { return position_ >= text_.size(); }

    [[nodiscard]] char peek() const {
        if (at_end())
            invalid("unexpected end of document");
        return text_[position_];
    }

    void skip_space() noexcept {
        while (!at_end()) {
            const char c = text_[position_];
            if (c != ' ' && c != '\t' && c != '\n' && c != '\r')
                return;
            ++position_;
        }
    }

    void expect_literal(std::string_view literal) {
        if (text_.substr(position_, literal.size()) != literal)
            invalid("invalid literal");
        position_ += literal.size();
    }

    Value parse_value(int depth) {
        if (depth > max_depth)
            invalid("document nests too deeply");
        const std::size_t start = position_;
        Value value;
        switch (peek()) {
        case '{':
            value = parse_object(depth);
            break;
        case '[':
            value = parse_array(depth);
            break;
        case '"':
            value.kind = Kind::string;
            value.string = parse_string();
            break;
        case 't':
            expect_literal("true");
            value.kind = Kind::boolean;
            value.boolean = true;
            break;
        case 'f':
            expect_literal("false");
            value.kind = Kind::boolean;
            break;
        case 'n':
            expect_literal("null");
            break;
        default:
            parse_number();
            if (!fits_double(text_.substr(start, position_ - start)))
                invalid("number does not fit a double");
            value.kind = Kind::number;
            break;
        }
        value.raw = text_.substr(start, position_ - start);
        return value;
    }

    Value parse_object(int depth) {
        Value value;
        value.kind = Kind::object;
        ++position_;
        skip_space();
        if (peek() == '}') {
            ++position_;
            return value;
        }
        std::unordered_set<std::string> keys;
        for (;;) {
            skip_space();
            if (peek() != '"')
                invalid("expected an object key");
            std::string key = parse_string();
            if (!keys.insert(key).second)
                invalid("duplicate key " + quote(key));
            skip_space();
            if (peek() != ':')
                invalid("expected ':' after an object key");
            ++position_;
            skip_space();
            value.members.emplace_back(std::move(key), parse_value(depth + 1));
            skip_space();
            const char c = peek();
            ++position_;
            if (c == '}')
                return value;
            if (c != ',')
                invalid("expected ',' or '}' in an object");
        }
    }

    Value parse_array(int depth) {
        Value value;
        value.kind = Kind::array;
        ++position_;
        skip_space();
        if (peek() == ']') {
            ++position_;
            return value;
        }
        for (;;) {
            skip_space();
            value.items.push_back(parse_value(depth + 1));
            skip_space();
            const char c = peek();
            ++position_;
            if (c == ']')
                return value;
            if (c != ',')
                invalid("expected ',' or ']' in an array");
        }
    }

    void parse_digits() {
        if (at_end() || text_[position_] < '0' || text_[position_] > '9')
            invalid("expected a digit");
        while (!at_end() && text_[position_] >= '0' && text_[position_] <= '9')
            ++position_;
    }

    void parse_number() {
        if (peek() == '-')
            ++position_;
        if (peek() == '0') {
            ++position_;
        } else {
            parse_digits();
        }
        if (!at_end() && text_[position_] == '.') {
            ++position_;
            parse_digits();
        }
        if (!at_end() && (text_[position_] == 'e' || text_[position_] == 'E')) {
            ++position_;
            if (!at_end() && (text_[position_] == '+' || text_[position_] == '-'))
                ++position_;
            parse_digits();
        }
    }

    unsigned parse_hex4() {
        if (text_.size() - position_ < 4)
            invalid("truncated unicode escape");
        unsigned value = 0;
        for (int i = 0; i < 4; ++i) {
            const char c = text_[position_++];
            value <<= 4;
            if (c >= '0' && c <= '9')
                value |= static_cast<unsigned>(c - '0');
            else if (c >= 'a' && c <= 'f')
                value |= static_cast<unsigned>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F')
                value |= static_cast<unsigned>(c - 'A' + 10);
            else
                invalid("invalid unicode escape");
        }
        return value;
    }

    static void append_utf8(std::string& out, unsigned code) {
        if (code < 0x80) {
            out += static_cast<char>(code);
        } else if (code < 0x800) {
            out += static_cast<char>(0xC0 | (code >> 6));
            out += static_cast<char>(0x80 | (code & 0x3F));
        } else if (code < 0x10000) {
            out += static_cast<char>(0xE0 | (code >> 12));
            out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (code & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (code >> 18));
            out += static_cast<char>(0x80 | ((code >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((code >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (code & 0x3F));
        }
    }

    // Copies one UTF-8 sequence, rejecting overlong forms and surrogates.
    void copy_utf8(std::string& out) {
        const auto lead = static_cast<unsigned char>(text_[position_]);
        int length = 0;
        unsigned code = 0;
        if (lead >= 0xC2 && lead <= 0xDF) {
            length = 2;
            code = lead & 0x1F;
        } else if (lead >= 0xE0 && lead <= 0xEF) {
            length = 3;
            code = lead & 0x0F;
        } else if (lead >= 0xF0 && lead <= 0xF4) {
            length = 4;
            code = lead & 0x07;
        } else {
            invalid("invalid UTF-8");
        }
        if (text_.size() - position_ < static_cast<std::size_t>(length))
            invalid("truncated UTF-8");
        for (int i = 1; i < length; ++i) {
            const auto next = static_cast<unsigned char>(text_[position_ + i]);
            if ((next & 0xC0) != 0x80)
                invalid("invalid UTF-8");
            code = (code << 6) | (next & 0x3F);
        }
        const bool overlong = (length == 3 && code < 0x800) || (length == 4 && code < 0x10000);
        if (overlong || code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF))
            invalid("invalid UTF-8");
        out.append(text_.substr(position_, length));
        position_ += length;
    }

    std::string parse_string() {
        ++position_;
        std::string out;
        for (;;) {
            const char c = peek();
            if (c == '"') {
                ++position_;
                return out;
            }
            if (static_cast<unsigned char>(c) < 0x20)
                invalid("control character in a string");
            if (static_cast<unsigned char>(c) >= 0x80) {
                copy_utf8(out);
                continue;
            }
            ++position_;
            if (c != '\\') {
                out += c;
                continue;
            }
            const char escape = peek();
            ++position_;
            switch (escape) {
            case '"':
                out += '"';
                break;
            case '\\':
                out += '\\';
                break;
            case '/':
                out += '/';
                break;
            case 'b':
                out += '\b';
                break;
            case 'f':
                out += '\f';
                break;
            case 'n':
                out += '\n';
                break;
            case 'r':
                out += '\r';
                break;
            case 't':
                out += '\t';
                break;
            case 'u': {
                unsigned code = parse_hex4();
                if (code >= 0xDC00 && code <= 0xDFFF)
                    invalid("unpaired surrogate");
                if (code >= 0xD800 && code <= 0xDBFF) {
                    if (text_.substr(position_, 2) != "\\u")
                        invalid("unpaired surrogate");
                    position_ += 2;
                    const unsigned low = parse_hex4();
                    if (low < 0xDC00 || low > 0xDFFF)
                        invalid("unpaired surrogate");
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                }
                append_utf8(out, code);
                break;
            }
            default:
                invalid("invalid escape");
            }
        }
    }

    std::string_view text_;
    std::size_t position_ = 0;
};

} // namespace

const Value* Value::find(std::string_view key) const {
    for (const auto& [name, value] : members) {
        if (name == key)
            return &value;
    }
    return nullptr;
}

Value parse(std::string_view text) {
    return Parser(text).document();
}

std::string quote(std::string_view text) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string out = "\"";
    for (const char c : text) {
        const auto byte = static_cast<unsigned char>(c);
        if (c == '"' || c == '\\') {
            out += '\\';
            out += c;
        } else if (byte < 0x20) {
            out += "\\u00";
            out += digits[byte >> 4];
            out += digits[byte & 0xF];
        } else {
            out += c;
        }
    }
    return out + "\"";
}

} // namespace taco::json
