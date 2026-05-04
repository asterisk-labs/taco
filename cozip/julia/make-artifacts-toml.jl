#!/usr/bin/env julia
#
# Regenerates cozip/julia/Artifacts.toml from the GH Release of
# v$(VERSION). VERSION defaults to cozip/VERSION but can be overridden
# with a CLI argument when you want to point at an older release
# (e.g. while staging a Julia-only patch against a previous C lib).
#
# Run from cozip/julia/ after the release exists on GitHub:
#   julia --project=. make-artifacts-toml.jl > Artifacts.toml
#   julia --project=. make-artifacts-toml.jl 2026.5.2.6 > Artifacts.toml
#
# For each platform, downloads the release asset, computes its sha256,
# extracts it, and computes the git-tree-sha1 of the tree (what Julia
# verifies on artifact install). Logs go to stderr, TOML to stdout.

using Pkg.Artifacts
using Downloads
using SHA

const REPO = "asterisk-labs/taco"
const VERSION = if !isempty(ARGS)
    strip(ARGS[1])
else
    strip(read(joinpath(@__DIR__, "..", "VERSION"), String))
end

# Each platform: TOML key/value lines + asset filename + archive kind.
# macOS gets two entries pointing at the same universal tarball.
const PLATFORMS = [
    (
        kv = ["arch = \"aarch64\"", "os = \"linux\"", "libc = \"glibc\""],
        asset = "libcozip-$(VERSION)-linux-aarch64.tar.gz",
        kind  = :tar,
    ),
    (
        kv = ["arch = \"x86_64\"", "os = \"linux\"", "libc = \"glibc\""],
        asset = "libcozip-$(VERSION)-linux-x86_64.tar.gz",
        kind  = :tar,
    ),
    (
        kv = ["arch = \"x86_64\"", "os = \"macos\""],
        asset = "libcozip-$(VERSION)-macos-universal.tar.gz",
        kind  = :tar,
    ),
    (
        kv = ["arch = \"aarch64\"", "os = \"macos\""],
        asset = "libcozip-$(VERSION)-macos-universal.tar.gz",
        kind  = :tar,
    ),
    (
        kv = ["arch = \"x86_64\"", "os = \"windows\""],
        asset = "libcozip-$(VERSION)-windows-x86_64.zip",
        kind  = :zip,
    ),
]


function fetch_and_hash(asset::String, kind::Symbol)
    url = "https://github.com/$REPO/releases/download/v$VERSION/$asset"
    @info "Fetching" url

    archive = Downloads.download(url)
    sha256_hex = bytes2hex(open(SHA.sha256, archive))

    tree = create_artifact() do dir
        cmd = if kind === :tar
            `tar -xzf $archive -C $dir`
        elseif kind === :zip
            `unzip -q $archive -d $dir`
        else
            error("unknown archive kind: $kind")
        end
        run(cmd)
        # No flattening: compute the tree hash of exactly what Julia
        # sees when it extracts the tarball into the artifact dir.
        # Each release archive wraps everything in a top-level
        # libcozip-VERSION-PLAT/ folder; LibCozip.jl descends into it
        # at runtime to find lib/cozip.{so,dylib,dll}.
    end

    return (sha256 = sha256_hex, tree = string(tree), url = url)
end


function main()
    for plat in PLATFORMS
        info = fetch_and_hash(plat.asset, plat.kind)

        println("[[cozip]]")
        for line in plat.kv
            println(line)
        end
        println("git-tree-sha1 = \"$(info.tree)\"")
        println("lazy = false")
        println()
        println("    [[cozip.download]]")
        println("    url = \"$(info.url)\"")
        println("    sha256 = \"$(info.sha256)\"")
        println()
    end
end


main()