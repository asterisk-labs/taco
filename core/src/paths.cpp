#include "paths.hpp"

#include <algorithm>
#include <cstdlib>
#include <memory>
#include <regex>
#include <set>

namespace taco {
namespace {

constexpr std::string_view parquet_suffix = ".parquet";

bool is_separator(char c) noexcept {
    return c == '/' || c == '\\';
}

std::string environment(const char* name) {
#if defined(_WIN32)
    char* value = nullptr;
    std::size_t size = 0;
    if (_dupenv_s(&value, &size, name) != 0)
        return {};
    const std::unique_ptr<char, decltype(&std::free)> owned(value, &std::free);
    return owned ? std::string(owned.get()) : std::string();
#else
    const char* value = std::getenv(name);
    return value ? std::string(value) : std::string();
#endif
}

std::string replace_all(std::string_view text, std::string_view from, std::string_view to) {
    std::string out;
    std::size_t start = 0;
    for (std::size_t found; (found = text.find(from, start)) != std::string_view::npos;
         start = found + from.size()) {
        out.append(text.substr(start, found - start));
        out.append(to);
    }
    out.append(text.substr(start));
    return out;
}

} // namespace

std::filesystem::path local_path(std::string_view utf8) {
    return std::filesystem::path(std::u8string(utf8.begin(), utf8.end()));
}

std::string utf8_path(const std::filesystem::path& path) {
    const auto text = path.generic_u8string();
    return std::string(text.begin(), text.end());
}

bool has_uri_scheme(std::string_view path) noexcept {
    const auto marker = path.find("://");
    if (marker == std::string_view::npos || marker == 0)
        return false;
    for (std::size_t i = 0; i < marker; ++i) {
        const char c = path[i];
        const bool alpha = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
        const bool other = (c >= '0' && c <= '9') || c == '+' || c == '-' || c == '.';
        if (!alpha && (i == 0 || !other))
            return false;
    }
    return true;
}

bool is_http_url(std::string_view uri) noexcept {
    return uri.starts_with("http://") || uri.starts_with("https://");
}

std::string encode_uri_path(std::string_view path) {
    static constexpr char digits[] = "0123456789ABCDEF";
    constexpr std::string_view marks = "-_.!~*'()/";
    std::string out;
    for (const char c : path) {
        const auto byte = static_cast<unsigned char>(c);
        if ((byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z') || (byte >= '0' && byte <= '9') ||
            marks.find(c) != std::string_view::npos) {
            out += c;
        } else {
            out += '%';
            out += digits[byte >> 4];
            out += digits[byte & 0xF];
        }
    }
    return out;
}

std::string file_uri_path(std::string_view uri) {
    std::string_view path = uri.substr(std::string_view("file://").size());
#ifdef _WIN32
    if (path.size() >= 3 && path[0] == '/' && path[2] == ':')
        path.remove_prefix(1);
#endif
    const auto hex = [](char c) {
        if (c >= '0' && c <= '9')
            return c - '0';
        if (c >= 'a' && c <= 'f')
            return c - 'a' + 10;
        if (c >= 'A' && c <= 'F')
            return c - 'A' + 10;
        return -1;
    };
    std::string out;
    for (std::size_t i = 0; i < path.size(); ++i) {
        const int high = path[i] == '%' && i + 2 < path.size() ? hex(path[i + 1]) : -1;
        const int low = high < 0 ? -1 : hex(path[i + 2]);
        // A '%' that starts no escape stays, and so does %00: no path holds a NUL.
        if (low < 0 || high + low == 0) {
            out += path[i];
        } else {
            out += static_cast<char>(high * 16 + low);
            i += 2;
        }
    }
    return out;
}

std::string_view without_query(std::string_view uri) noexcept {
    if (!has_uri_scheme(uri))
        return uri;
    return uri.substr(0, uri.find_first_of("?#"));
}

std::string redact_uri(std::string_view uri) {
    std::string result(uri);
    const auto scheme = result.find("://");
    if (scheme != std::string::npos) {
        const auto authority = scheme + 3;
        const auto authority_end = result.find_first_of("/?#", authority);
        const auto at = result.find('@', authority);
        if (at != std::string::npos && (authority_end == std::string::npos || at < authority_end))
            result.replace(authority, at - authority, "<redacted>");
    }
    const auto query = result.find('?');
    const auto fragment = result.find('#');
    const auto secret = std::min(query, fragment);
    if (secret != std::string::npos) {
        const bool had_query = query == secret;
        result.resize(secret);
        result += had_query ? "?<redacted>" : "#<redacted>";
    }
    return result;
}

bool is_zip_name(std::string_view path) noexcept {
    const auto object = without_query(path);
    if (object.size() < 4)
        return false;
    auto suffix = std::string(object.substr(object.size() - 4));
    std::transform(suffix.begin(), suffix.end(), suffix.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return suffix == ".zip";
}

bool is_semver(std::string_view text) {
    static const std::regex pattern(
        R"(^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))"
        R"((-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$)");
    return std::regex_match(text.begin(), text.end(), pattern);
}

std::string trim_trailing_slashes(std::string_view path) {
    while (path.size() > 1 && is_separator(path.back()))
        path.remove_suffix(1);
    return std::string(path);
}

std::string child_path(std::string_view directory, std::string_view child) {
    return trim_trailing_slashes(directory) + "/" + std::string(child);
}

std::string parent_path(std::string_view path) {
    const std::string trimmed = trim_trailing_slashes(without_query(path));
    const auto slash = std::find_if(trimmed.rbegin(), trimmed.rend(), is_separator);
    if (slash == trimmed.rend())
        return ".";
    return trimmed.substr(0, static_cast<std::size_t>(trimmed.rend() - slash) - 1);
}

std::string source_name(std::string_view source) {
    const std::string trimmed = trim_trailing_slashes(without_query(source));
    const auto slash = std::find_if(trimmed.rbegin(), trimmed.rend(), is_separator);
    return slash == trimmed.rend() ? trimmed
                                   : trimmed.substr(static_cast<std::size_t>(trimmed.rend() - slash));
}

std::vector<std::string> source_labels(const std::vector<std::string>& sources) {
    std::vector<std::string> names;
    std::set<std::string> unique;
    for (const auto& source : sources) {
        names.push_back(source_name(source));
        unique.insert(names.back());
    }
    const bool usable = unique.size() == names.size() &&
                        std::none_of(names.begin(), names.end(),
                                     [](const std::string& name) { return name.empty(); });
    return usable ? names : sources;
}

bool is_partition_path(std::string_view path) {
    if (!path.ends_with(".zip") || path.find_first_of(std::string_view("\\\0", 2)) != std::string_view::npos)
        return false;
    for (std::size_t start = 0;;) {
        const auto slash = path.find('/', start);
        const auto component = path.substr(start, slash == std::string_view::npos ? slash : slash - start);
        if (component.empty() || component == "." || component == "..")
            return false;
        if (slash == std::string_view::npos)
            return true;
        start = slash + 1;
    }
}

std::string level_to_file(std::string_view level) {
    return replace_all(level, "/", "__") + std::string(parquet_suffix);
}

std::string parent_level(std::string_view level) {
    if (level == "children")
        return "sample";
    const auto slash = level.rfind('/');
    return slash == std::string_view::npos ? "sample" : std::string(level.substr(0, slash));
}

std::string last_segment(std::string_view level) {
    const auto slash = level.rfind('/');
    return std::string(slash == std::string_view::npos ? level : level.substr(slash + 1));
}

std::uint64_t fnv1a64(std::string_view data, std::uint64_t hash) noexcept {
    for (const char c : data) {
        hash ^= static_cast<unsigned char>(c);
        hash *= 0x100000001B3ULL;
    }
    return hash;
}

std::string hex64(std::uint64_t value) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string out(16, '0');
    for (int i = 15; i >= 0; --i, value >>= 4)
        out[static_cast<std::size_t>(i)] = digits[value & 0xF];
    return out;
}

std::string default_cache_dir() {
    if (auto configured = environment("TACO_CACHE_DIR"); !configured.empty())
        return configured;
#if defined(_WIN32)
    if (auto local = environment("LOCALAPPDATA"); !local.empty())
        return local + "\\taco\\cache";
#else
    if (auto xdg = environment("XDG_CACHE_HOME"); !xdg.empty())
        return xdg + "/taco";
    if (auto home = environment("HOME"); !home.empty())
        return home + "/.cache/taco";
#endif
    return ".taco-cache";
}

std::string sql_identifier(std::string_view name) {
    return "\"" + replace_all(name, "\"", "\"\"") + "\"";
}

std::string sql_literal(std::string_view value) {
    return "'" + replace_all(value, "'", "''") + "'";
}

// DuckDB does not take a backslash as an escape here, but a one-character
// class matches its character literally.
std::string glob_escape(std::string_view path) {
    std::string out;
    for (const char c : path) {
        if (c == '[' || c == ']' || c == '?' || c == '*')
            out += {'[', c, ']'};
        else
            out += c;
    }
    return out;
}

} // namespace taco
