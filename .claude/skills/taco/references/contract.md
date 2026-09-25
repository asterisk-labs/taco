# Contract: structure and naming

Sources: `python/taco/contract/structure.py`, `contract/naming.py`,
`contract/contract.py`, `contract/sample.py`, SPEC 5.1 to 5.3.

The contract is `taco:structure` plus `taco:metadata`. It is declared once, stored in
`COLLECTION.json`, and never changes. Two contracts are equal when their serialized
form matches: structure order, level names, qualified fields, types, nullability and
descriptions. Python class names, defaults and validators are not part of it.

```python
contract = taco.Contract(
    structure=["before/B02.tif", "before/B03.tif", "after/B02.tif", "mask.tif"],
    metadata=[...],   # taco.Level objects, or a plain mapping, see metadata.md
)
contract.levels      # ('sample', 'children', 'children/before', 'children/after')
contract.leaves      # parsed Leaf objects, in declaration order
contract.folders     # frozenset of folder tuples
contract.to_dict()   # the serialized form used for equality and COLLECTION.json
```

## Declarations

Every path is a **normalized relative POSIX path**: `/` as separator, no empty
component, no `.` or `..`, no leading or trailing slash, no backslash. Every
component is printable ASCII so one contract is valid in FOLDER and ZIP.

A **fixed file** appears exactly once per sample and is written literally:
`s2_l1c.tif`, `before/B02.tif`.

A **variable sequence** is `prefix*[min,max].ext` with `1 <= min <= max`. A sample
holding `k` files names them `prefix0.ext` to `prefix{k-1}.ext`, contiguous, with
`min <= k <= max`.

```python
taco.Contract(structure=["img*[4,16].tif"])       # img0.tif .. img15.tif
taco.Contract(structure=["before/mask*[1,5].tif"])
```

The `*` is a **cardinal index, not a glob**. `Leaf.match_index` rejects a non-digit
middle, a leading zero and an index at or above `maximum`:

| Name | `img*[2,4].png` |
| --- | --- |
| `img0.png` | index 0 |
| `img3.png` | index 3 |
| `img01.png` | no match, leading zero |
| `img4.png` | no match, `index < maximum` fails |
| `imgA.png` | no match |

A name that does not match is an undeclared file, but the count check fires first, so
a leading zero reports `'img*[2,4].png' requires contiguous indexes with 2 <= count <=
4; got [0]` rather than naming the offending file. Check the index spelling when a
count error looks impossible.

## Uniqueness inside a folder

Entries in one folder must have distinct **identifiers**: the folder name, the full
fixed filename, or the prefix of a variable sequence. Identifiers and the overlap
checks below ignore letter case, because a case-insensitive file system or a SQL
identifier would merge `B02.tif` and `b02.tif`.

```
ContractError: children under the sample root must have distinct identifiers
ContractError: 'Before' and 'before' under the sample root differ only in case
ContractError: 'img3.tif' overlaps 'img*[1,9].tif' under the sample root
```

Two sequences sharing a prefix fail the identifier check. Two sequences with
different prefixes that can still spell the same name are caught by
`variable_sequences_overlap` in `naming.py`, which walks both patterns position by
position with a digit-width automaton rather than sampling names, so it is exact:

| Pair | Verdict |
| --- | --- |
| `a*[1,100].tif`, `a1*[1,10].tif` | overlap: both produce `a15.tif` |
| `a*[1,9].tif`, `a1*[1,9].tif` | accepted: the first stops at `a8.tif`, one digit |
| `img*[1,9].tif`, `im*[1,99].tif` | accepted: `g0` is not a digit run |
| `a*[1,9].tif`, `ab*[1,9].tif` | accepted: `b0` is not a digit run |

## Reserved characters

| Token | Rule |
| --- | --- |
| `:` | Exactly one in every user field. Never in a folder name, file name or level key. |
| `/` | Separates path and level segments. Never inside a component. |
| `__` | Replaces `/` in Parquet filenames. Never in a name, namespace or field. |

A component may not contain `<>:"\|?*[]`, may not end with a space or a dot, and may
not be `.` or `..`. `*`, `[` and `]` are freed only in the last component of a
structure declaration, where a variable sequence may use them.

```
ContractError: structure path component 'a__b.tif' must not contain '__'
ContractError: structure path must use '/' as separator: 'a\\b.tif'
```

## Levels come from folders

`Contract._derive_levels` walks the folder tree: `sample`, `children`, then one
`children/<folder-path>` per folder, shallowest first and, within a depth, in
declaration order. A structure with no folders has exactly `sample` and `children`.

`children/before` holds one row per direct child of every `before/` folder, across all
samples. A nested folder adds `children/before/bands`, whose parent is
`children/before`.

Note the two orders: `Contract.levels` is declaration order, while the core sorts its
own list. See reading.md.

## Samples, assets and folders

```python
sample = taco.Sample(
    id="lima-0001",                                  # non-empty, unique in the dataset
    assets=[...],                                    # Asset, a source, or a sequence
    metadata=taco.Metadata(ml=ML(split="train")),    # sample-level groups
    folders=[taco.Folder("before", metadata=...)],   # only folders that carry metadata
)
```

`taco.Asset(source, *, path=None, metadata=None)` takes a path or raw `bytes`. `path`
is the contract declaration the file satisfies.

- `path` is **required** for a nested declaration and whenever the source filename
  differs from the declaration. Inline bytes need it too, unless the contract has a
  single fixed root leaf: `inline assets need an explicit contract path`.
- It is otherwise inferred from a real file whose basename matches exactly one
  root-level leaf. Zero or several matches give
  `cannot infer a unique contract path from 'B02.tif'; pass path=`.
- Inference never looks inside folders: a `before/B02.tif` declaration is only
  satisfied by `taco.Asset(source, path="before/B02.tif")`.

`Folder` is only needed when a folder carries metadata; the structure already implies
it. Naming a folder absent from the contract, or attaching empty metadata, is refused:

```
SampleError: sample names folders absent from the contract: ['after']
SampleError: empty folder metadata is unnecessary: ['before']
```

`Contract.expand()` resolves a sample into the ordered node tree the writer stores:
folders and fixed files in declaration order, sequence instances by numeric index.
That order becomes `internal:current_id` in the child tables.

## Sample errors

| Message | Cause |
| --- | --- |
| `required asset 'before/B02.tif' is missing` | A fixed declaration has no asset |
| `'img*[2,4].png' requires contiguous indexes with 2 <= count <= 4; got [0, 2]` | Gap, wrong count, or an unmatched spelling |
| `assets do not match the structure: ['zzz.tif']` | An asset satisfies no declaration |
| `duplicate asset path 'mask.tif'` | Two assets claim the same declaration |
| `zero-byte assets are not allowed: <path>` | Every stored file needs one byte |
| `sample id must be a non-empty string` | `id` missing or blank |
| `sample id 's0' appears more than once` | Duplicate within one writer |
| `metadata group 'ml' is required at 'sample'` | A non-optional group was omitted |
| `metadata groups at 'children' are not declared: ['x']` | A group the level does not declare |
| `generated extension groups cannot be supplied: ['rumi']` | An output-only extension was passed a value |

## From and to `COLLECTION.json`

`Contract.from_dict` requires `taco:structure` and `taco:metadata`, and every
serialized field to declare exactly `type`, `nullable` and `description`. It also
requires `taco:metadata` to contain every level the structure implies, so a contract
cannot silently lose a level. Readers accept the legacy `taco:derived` descriptor and
re-validate its outputs, but the writer does not persist extension descriptors in new
datasets.
