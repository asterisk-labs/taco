#include "sql.hpp"

#include "error.hpp"
#include "paths.hpp"

#include <algorithm>
#include <cstring>
#include <exception>

namespace taco {
namespace {

// TACO spec 7.2. Users may not define columns with this prefix, so the
// builder can hide them from the projected metadata without collisions.
constexpr const char* id_current = "internal:current_id";
constexpr const char* id_parent = "internal:parent_id";
constexpr const char* id_path = "internal:relative_path";
constexpr const char* id_offset = "internal:offset";
constexpr const char* id_size = "internal:size";
constexpr const char* id_source = "internal:source_file";
constexpr const char* logical_id = "id";
constexpr const char* sample_index = "sample_index";
constexpr const char* location_column = "taco:location";
constexpr const char* flat_location_column = "cozip:location";
// Rumi assets are read statelessly with their header, so a wide read carries
// it next to the location of every leaf whose level declares it.
constexpr const char* header_field = "rumi:header";
constexpr const char* data_directory = "DATA";

std::string regex_escape(std::string_view value) {
    std::string out;
    for (const char c : value) {
        if (std::strchr(".^$|()[]{}*+?\\/", c))
            out += '\\';
        out += c;
    }
    return out;
}

// A structure leaf is a literal name or prefix*[min,max]suffix.
struct Leaf {
    std::string declaration;
    bool variable = false;
    std::string prefix;
    std::string suffix;
};

Leaf parse_leaf(const std::string& declaration) {
    Leaf leaf;
    leaf.declaration = declaration;
    const auto star = declaration.find('*');
    if (star == std::string::npos)
        return leaf;
    const auto open = declaration.find('[', star);
    const auto close = declaration.find(']', open == std::string::npos ? star : open);
    if (open != star + 1 || close == std::string::npos)
        fail("malformed variable leaf in taco:structure: " + declaration);
    leaf.variable = true;
    leaf.prefix = declaration.substr(0, star);
    leaf.suffix = declaration.substr(close + 1);
    return leaf;
}

// The metadata level that holds the rows of a leaf: its folder under children.
std::string leaf_level(const Leaf& leaf) {
    const auto& path = leaf.variable ? leaf.prefix : leaf.declaration;
    const auto slash = path.rfind('/');
    return slash == std::string::npos ? "children" : "children/" + path.substr(0, slash);
}

std::string output_name(const Leaf& leaf) {
    auto name = leaf.variable ? leaf.prefix : leaf.declaration;
    std::string out;
    out.reserve(name.size());
    for (const char c : name) {
        if (c == '/')
            out += "__";
        else
            out += c;
    }
    return out;
}

std::string index_filter(const std::string& selection, const std::string& column) {
    if (selection.empty())
        return "";
    const auto invalid = [&](const char* reason) {
        fail(std::string("taco: idx must ") + reason + ", got " + selection);
    };
    const auto trim = [](std::string value) {
        const auto first = value.find_first_not_of(" \t");
        const auto last = value.find_last_not_of(" \t");
        return first == std::string::npos ? std::string() : value.substr(first, last - first + 1);
    };
    std::string text = trim(selection);
    const bool range = !text.empty() && text.front() == '[';
    if (range) {
        if (text.back() != ']')
            invalid("be an integer or a two-element list");
        text = text.substr(1, text.size() - 2);
    } else if (text.find_first_of("[],") != std::string::npos) {
        invalid("be an integer or a two-element list");
    }

    std::vector<std::string> parts;
    for (std::size_t start = 0;;) {
        const auto comma = text.find(',', start);
        parts.push_back(trim(text.substr(start, comma == std::string::npos ? comma : comma - start)));
        if (comma == std::string::npos)
            break;
        start = comma + 1;
    }
    if ((!range && parts.size() != 1) || (range && parts.size() != 2))
        invalid("be an integer or a two-element list");

    std::vector<std::uint64_t> bounds;
    for (const auto& part : parts) {
        if (part.empty())
            invalid("be an integer or a two-element list");
        if (part.find_first_not_of("0123456789") != std::string::npos)
            invalid("contain non-negative integers");
        try {
            bounds.push_back(std::stoull(part));
        } catch (const std::exception&) {
            invalid("be an integer or a two-element list");
        }
    }
    if (bounds.size() == 1)
        return column + " = " + std::to_string(bounds[0]);
    if (bounds[0] > bounds[1])
        fail("taco: idx range start must not exceed its end, got " + selection);
    return column + " >= " + std::to_string(bounds[0]) + " AND " + column + " < " +
           std::to_string(bounds[1]);
}

class QueryBuilder {
  public:
    QueryBuilder(const Dataset& dataset, const ReadOptions& options)
        : dataset_(dataset), options_(options), tacocat_(dataset.container == Container::tacocat),
          has_offsets_(dataset.container != Container::folder) {}

    [[nodiscard]] std::string level_query() const {
        const auto level = dataset_.level_index(options_.level);
        return "SELECT " + metadata_projection() + " FROM read_parquet(" +
               sql_literal(dataset_.level_paths[level]) + ")";
    }

    [[nodiscard]] std::string flat_query() const {
        std::string branches;
        if (options_.has_files) {
            const auto leaves = selected_leaves();
            branches = flat_branches(false, &leaves);
        } else {
            branches = flat_branches(false);
        }
        std::string out = "SELECT ";
        if (tacocat_)
            out += "source_file, ";
        out += std::string(sample_index) + ", id, path";
        if (options_.location)
            out += ", " + sql_identifier(location_column);
        std::vector<std::string> fields;
        for (const auto& [level, names] : dataset_.contract.fields) {
            for (const auto& name : names) {
                if (std::find(fields.begin(), fields.end(), name) == fields.end())
                    fields.push_back(name);
            }
        }
        std::sort(fields.begin(), fields.end());
        for (const auto& name : fields)
            out += ", " + sql_identifier(name);
        return common_table_expressions() + "\n" + out + " FROM (" + branches + ") AS taco_files";
    }

    [[nodiscard]] std::string pivot_query() const {
        const auto leaves = selected_leaves();
        // Metadata-only reads stop at sample.parquet. Placeholder columns keep
        // the wide schema stable without touching any child level.
        if (!options_.location) {
            std::string out = "SELECT ";
            if (tacocat_)
                out += alias(0) + "." + sql_identifier(id_source) + " AS source_file, ";
            out += alias(0) + "." + sql_identifier(id_current) + " AS " + sample_index + ", " + alias(0) + "." +
                   sql_identifier(logical_id) + " AS id, " + alias(0) + ".*" + exclude_list(0);
            for (const auto& leaf : leaves) {
                out += leaf.variable ? ", NULL::VARCHAR[] AS " : ", NULL::VARCHAR AS ";
                out += sql_identifier(location_name(leaf));
                if (has_header(leaf)) {
                    out += leaf.variable ? ", NULL::BLOB[] AS " : ", NULL::BLOB AS ";
                    out += sql_identifier(header_name(leaf));
                }
            }
            out += " FROM (SELECT " + metadata_projection() + " FROM read_parquet(" +
                   sql_literal(dataset_.level_paths[0]) + ")) AS " + alias(0);
            const auto idx = idx_filter(alias(0));
            if (!idx.empty())
                out += " WHERE " + idx;
            return out;
        }

        // Build the file relation once, pivot its locations by contract leaf,
        // then attach those values to every sample. The LEFT JOIN preserves
        // samples whose optional files are absent.
        std::string out = common_table_expressions();
        out += ", flat AS (\n" + flat_branches(true, options_.has_files ? &leaves : nullptr) + "\n)";
        out += ", pivoted AS (SELECT " + std::string(sample_index);
        if (tacocat_)
            out += ", source_file";
        for (const auto& leaf : leaves) {
            out += pivot_column(leaf, location_column, location_name(leaf));
            if (has_header(leaf))
                out += pivot_column(leaf, header_field, header_name(leaf));
        }
        out += " FROM flat GROUP BY ALL)";

        out += "\nSELECT ";
        if (tacocat_)
            out += alias(0) + "." + sql_identifier(id_source) + " AS source_file, ";
        out += alias(0) + "." + sql_identifier(id_current) + " AS " + sample_index + ", " + alias(0) + "." +
               sql_identifier(logical_id) + " AS id, " + alias(0) + ".*" + exclude_list(0);
        for (const auto& leaf : leaves) {
            const auto location = location_name(leaf);
            if (leaf.variable)
                out += ", COALESCE(p." + sql_identifier(location) + ", []::VARCHAR[]) AS " + sql_identifier(location);
            else
                out += ", p." + sql_identifier(location);
            if (!has_header(leaf))
                continue;
            const auto header = header_name(leaf);
            if (leaf.variable)
                out += ", COALESCE(p." + sql_identifier(header) + ", []::BLOB[]) AS " + sql_identifier(header);
            else
                out += ", p." + sql_identifier(header);
        }
        out += " FROM " + alias(0) + " LEFT JOIN pivoted p ON p." + sample_index + " = " + alias(0) + "." +
               sql_identifier(id_current);
        const auto idx = idx_filter(alias(0));
        if (!idx.empty())
            out += " WHERE " + idx;
        return out;
    }

  private:
    static std::string alias(std::size_t level) { return "l" + std::to_string(level); }

    // Metadata fields contain exactly one ':'. The double separator keeps
    // generated columns outside that namespace without hiding the leaf name.
    static std::string location_name(const Leaf& leaf) { return output_name(leaf) + "::location"; }
    static std::string header_name(const Leaf& leaf) { return output_name(leaf) + "::header"; }

    [[nodiscard]] bool has_header(const Leaf& leaf) const {
        const auto* fields = dataset_.contract.fields_of(leaf_level(leaf));
        return fields && std::find(fields->begin(), fields->end(), header_field) != fields->end();
    }

    // One pivoted value of a leaf: the value itself, or the list of a
    // variable sequence ordered by its numeric index.
    static std::string pivot_column(const Leaf& leaf, const std::string& column, const std::string& name) {
        if (!leaf.variable) {
            return ", MAX(CASE WHEN path = " + sql_literal(leaf.declaration) + " THEN " + sql_identifier(column) +
                   " END) AS " + sql_identifier(name);
        }
        // TACO spec 5.2: the index has no leading zeros, so img01.tif is not an
        // instance of img*[a,b].tif.
        const auto pattern = variable_pattern(leaf);
        return ", list(" + sql_identifier(column) + " ORDER BY TRY_CAST(regexp_extract(path, " + sql_literal(pattern) +
               ", 1) AS BIGINT)) FILTER (WHERE regexp_matches(path, " + sql_literal(pattern) + ")) AS " +
               sql_identifier(name);
    }

    static std::string metadata_projection() {
        return "COLUMNS(lambda c: c != " + sql_literal(flat_location_column) + " AND c != " +
               sql_literal(location_column) + ")";
    }

    // Columns the reader owns and therefore hides from projected metadata.
    [[nodiscard]] std::string exclude_list(std::size_t level) const {
        std::vector<std::string> columns = {id_current, id_path};
        if (level == 0)
            columns.push_back(logical_id);
        if (level > 0)
            columns.push_back(id_parent);
        if (has_offsets_ && level > 0) {
            columns.push_back(id_offset);
            columns.push_back(id_size);
        }
        if (tacocat_)
            columns.push_back(id_source);
        std::string out = " EXCLUDE (";
        for (std::size_t i = 0; i < columns.size(); ++i)
            out += (i ? ", " : "") + sql_identifier(columns[i]);
        return out + ")";
    }

    // Where one data row can be read from.
    [[nodiscard]] std::string location_expression(std::size_t level) const {
        const auto row = alias(level);
        if (dataset_.container == Container::folder)
            return sql_literal(dataset_.location_base + "/" + data_directory + "/") + " || " + row + "." +
                   sql_identifier(id_path);
        const std::string archive =
            tacocat_ ? sql_literal(dataset_.location_base + "/") + " || " + row + "." + sql_identifier(id_source)
                     : sql_literal(dataset_.location_base);
        return "'/vsisubfile/' || " + row + "." + sql_identifier(id_offset) + " || '_' || " + row + "." +
               sql_identifier(id_size) + " || ',' || " + archive;
    }

    // The contract path: internal:relative_path without the sample index.
    static std::string path_expression(std::size_t level) {
        return "regexp_replace(" + alias(level) + "." + sql_identifier(id_path) + ", '^[0-9]+/', '')";
    }

    // Folder rows carry no bytes. A child level proves a name is a folder.
    [[nodiscard]] std::string file_filter(std::size_t level) const {
        std::vector<std::string> folders;
        for (std::size_t i = 1; i < dataset_.level_names.size(); ++i) {
            if (parent_level(dataset_.level_names[i]) == dataset_.level_names[level])
                folders.push_back(last_segment(dataset_.level_names[i]));
        }
        if (folders.empty())
            return "";
        std::string out = "regexp_replace(" + alias(level) + "." + sql_identifier(id_path) + ", '^.*/', '') NOT IN (";
        for (std::size_t i = 0; i < folders.size(); ++i)
            out += (i ? ", " : "") + sql_literal(folders[i]);
        return out + ")";
    }

    static std::string variable_pattern(const Leaf& leaf) {
        return "^" + regex_escape(leaf.prefix) + "(0|[1-9][0-9]*)" + regex_escape(leaf.suffix) + "$";
    }

    [[nodiscard]] std::string selected_files_filter(std::size_t level, const std::vector<Leaf>& leaves) const {
        const auto path = path_expression(level);
        std::string out = "(";
        for (std::size_t i = 0; i < leaves.size(); ++i) {
            if (i)
                out += " OR ";
            if (leaves[i].variable)
                out += "regexp_matches(" + path + ", " + sql_literal(variable_pattern(leaves[i])) + ")";
            else
                out += path + " = " + sql_literal(leaves[i].declaration);
        }
        return out + ")";
    }

    // The user columns of level and of every ancestor, deepest first. A field
    // redeclared higher up is hidden, so the row carries the value of the
    // level it belongs to.
    [[nodiscard]] std::string ancestor_projection(std::size_t level) const {
        std::vector<std::size_t> chain;
        for (auto node = level; node > 0; node = dataset_.level_index(parent_level(dataset_.level_names[node])))
            chain.push_back(node);
        chain.push_back(0);

        std::vector<std::string> seen;
        std::vector<std::pair<std::string, std::size_t>> selected;
        for (const auto index : chain) {
            if (const auto* declared = dataset_.contract.fields_of(dataset_.level_names[index])) {
                for (const auto& name : *declared) {
                    if (std::find(seen.begin(), seen.end(), name) == seen.end()) {
                        seen.push_back(name);
                        selected.emplace_back(name, index);
                    }
                }
            }
        }
        std::sort(selected.begin(), selected.end(), [](const auto& left, const auto& right) {
            return left.first < right.first;
        });
        std::string out;
        for (const auto& [name, index] : selected)
            out += ", " + alias(index) + "." + sql_identifier(name);
        return out;
    }

    // Joins from level up to the sample level.
    [[nodiscard]] std::string join_chain(std::size_t level) const {
        std::string out;
        for (auto child = level; child > 0;) {
            const auto parent = dataset_.level_index(parent_level(dataset_.level_names[child]));
            out += " JOIN " + alias(parent) + " ON " + alias(child) + "." + sql_identifier(id_parent) + " = " +
                   alias(parent) + "." + sql_identifier(id_current);
            child = parent;
        }
        return out;
    }

    [[nodiscard]] std::string idx_filter(const std::string& row) const {
        return index_filter(options_.idx, row + "." + sql_identifier(id_current));
    }

    [[nodiscard]] std::string common_table_expressions() const {
        // Naming every level once keeps the generated joins readable and lets
        // DuckDB plan all Parquet scans as one statement.
        std::string out = "WITH ";
        for (std::size_t i = 0; i < dataset_.level_paths.size(); ++i) {
            out += (i ? ", " : "") + alias(i) + " AS (SELECT " + metadata_projection() + " FROM read_parquet(" +
                   sql_literal(dataset_.level_paths[i]) + "))";
        }
        return out;
    }

    // One row per data file, columns aligned across levels by name.
    [[nodiscard]] std::string flat_branches(bool identity_only, const std::vector<Leaf>* selected = nullptr) const {
        std::string out;
        // Level zero is sample metadata. Payload files begin at child levels.
        for (std::size_t level = 1; level < dataset_.level_names.size(); ++level) {
            if (!out.empty())
                out += "\nUNION ALL BY NAME\n";
            out += "SELECT ";
            if (tacocat_)
                out += alias(0) + "." + sql_identifier(id_source) + " AS source_file, ";
            out += alias(0) + "." + sql_identifier(id_current) + " AS " + sample_index + ", " + alias(0) + "." +
                   sql_identifier(logical_id) + " AS id, " + path_expression(level) + " AS path";
            if (identity_only || options_.location)
                out += ", " + location_expression(level) + " AS " + sql_identifier(location_column);
            if (identity_only) {
                const auto* fields = dataset_.contract.fields_of(dataset_.level_names[level]);
                if (fields && std::find(fields->begin(), fields->end(), header_field) != fields->end())
                    out += ", " + alias(level) + "." + sql_identifier(header_field);
            }
            if (!identity_only)
                out += ancestor_projection(level);
            out += " FROM " + alias(level) + join_chain(level);

            std::vector<std::string> filters;
            if (auto files = file_filter(level); !files.empty())
                filters.push_back(std::move(files));
            if (auto idx = idx_filter(alias(0)); !idx.empty())
                filters.push_back(std::move(idx));
            if (selected)
                filters.push_back(selected_files_filter(level, *selected));
            for (std::size_t i = 0; i < filters.size(); ++i)
                out += (i ? " AND " : " WHERE ") + filters[i];
        }
        return out;
    }

    [[nodiscard]] std::vector<Leaf> selected_leaves() const {
        const auto& structure = dataset_.contract.structure;
        std::string unknown;
        for (const auto& name : options_.files) {
            if (std::find(structure.begin(), structure.end(), name) == structure.end())
                unknown += (unknown.empty() ? "" : ", ") + name;
        }
        if (!unknown.empty())
            fail("taco: files contains unknown structure leaf: " + unknown);

        std::vector<Leaf> leaves;
        for (const auto& declaration : structure) {
            if (options_.has_files &&
                std::find(options_.files.begin(), options_.files.end(), declaration) == options_.files.end())
                continue;
            leaves.push_back(parse_leaf(declaration));
        }
        if (leaves.empty())
            fail("taco: no structure leaf matches the requested files");
        return leaves;
    }

    const Dataset& dataset_;
    const ReadOptions& options_;
    bool tacocat_;
    bool has_offsets_;
};

} // namespace

std::string build_sql(const Dataset& dataset, const ReadOptions& options) {
    const QueryBuilder builder(dataset, options);
    if (!options.level.empty()) {
        if (options.has_files)
            fail("taco: files does not apply when level is set");
        return builder.level_query();
    }
    if (!options.pivot)
        return builder.flat_query();
    return builder.pivot_query();
}

std::string build_union_sql(const std::vector<const Dataset*>& datasets, const ReadOptions& options) {
    if (datasets.empty())
        fail("taco: a read needs at least one dataset");
    if (datasets.size() == 1)
        return build_sql(*datasets.front(), options);

    std::vector<std::string> sources;
    for (const auto* dataset : datasets)
        sources.push_back(dataset->source);
    const auto labels = source_labels(sources);
    ReadOptions partition_options = options;
    if (options.level.empty())
        partition_options.idx.clear();

    std::string out;
    for (std::size_t i = 0; i < datasets.size(); ++i) {
        if (i)
            out += "\nUNION ALL BY NAME\n";
        const auto label = sql_literal(labels[i]);
        if (options.level.empty())
            out += "SELECT " + std::to_string(i) + " AS internal_source_order, " + label +
                   " AS source_file, taco.*";
        else
            out += "SELECT " + label + " AS source_file, taco.*";
        out += " FROM (" + build_sql(*datasets[i], partition_options) + ") AS taco";
    }
    if (!options.level.empty())
        return out;
    const std::string indexed =
        "SELECT source_file, CAST(dense_rank() OVER (ORDER BY internal_source_order, sample_index) - 1 AS "
        "UBIGINT) AS sample_index, * EXCLUDE (internal_source_order, source_file, sample_index) FROM (" +
        out + ") AS taco_union";
    std::string result = "SELECT * FROM (" + indexed + ") AS taco_global";
    if (const auto filter = index_filter(options.idx, "taco_global." + sql_identifier(sample_index)); !filter.empty())
        result += " WHERE " + filter;
    return result + " ORDER BY sample_index";
}

} // namespace taco
