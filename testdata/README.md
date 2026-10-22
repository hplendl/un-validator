# UN Validator test data

These are small, public test datasets for [un-validator](https://github.com/hplendl/un-validator) v0.3.0. The validator reads **File Geodatabases (.gdb)** and **GeoPackages (.gpkg)**, so those are the two formats here. Shapefiles and .ppkx packages are not read directly.

## Source and license
All of the data comes from the Esri **Electric Transmission Data Migration Tutorial Sample Data** (Naperville, IL, fictitious network):
https://www.arcgis.com/home/item.html?id=89d7054c6f0b46d7aad36aa3678967d3. It is owned by arcgis_solutions (Esri) and licensed under **Apache-2.0**.
The derived and seeded copies were modified by un-validator contributors, as Apache-2.0 §4(b) requires to be stated. There is no personal or work data.
Other public samples (Foundation asset packages, Pro SDK built-UN gdbs, Langley, SeaTac and Naperville city data) are not bundled here. Fetch them from the URLs in the repo's `DATA_SOURCES.md`.

## Contents
| Path | What it is |
|---|---|
| `clean/Transmission_Naperville_SourceData.gdb` | Original Esri FGDB with domains and subtypes (12 classes, about 4,900 features). The match-table CSVs sit next to it for the migration field-mapping check |
| `clean/Transmission_Naperville.gpkg` | The same layers converted to GeoPackage (no Esri domains, M dropped) |
| `seeded/Transmission_seeded.gdb` | FGDB copy with 8 appended bad rows (G01–G08) |
| `seeded/Transmission_seeded.gpkg` | GeoPackage copy with 9 planted errors (P01–P09) |
| `errors_manifest.csv` | Every planted error: id, dataset, layer, type, description, and the check expected to catch it |
| `expected_results.csv` | The manifest plus the result from v0.3.0 (CAUGHT, MISSED or NOT PLANTED) and the evidence |
| `tools/build_testdata.py` | Rebuilds everything from the original Esri download (`python tools/build_testdata.py <path to source gdb>`) |
| `results_raw/` | Full HTML/CSV/JSON reports from the reference runs |

## How to run
```bat
python run.py --allow-any-path validate testdata\clean\Transmission_Naperville_SourceData.gdb
python run.py --allow-any-path validate testdata\clean\Transmission_Naperville.gpkg
python run.py --allow-any-path validate testdata\seeded\Transmission_seeded.gdb
python run.py --allow-any-path validate testdata\seeded\Transmission_seeded.gpkg
```
You can also run `python run.py serve` and point the UI at the `testdata` folder to run them all as a folder comparison.

## Expected scores (v0.4.0)
| Dataset | Overall | Metadata | Lineage | Schema | Quality | Critical/Error/Warn/Info |
|---|---|---|---|---|---|---|
| clean .gdb | 76.9 C | 20.8 | 82.4 | 84.9 | 98.0 | 0 / 8 / 18 / 12 |
| clean .gpkg | 69.7 D | 16.7 | 82.4 | 58.3 | 98.5 | 0 / 4 / 15 / 7 |
| seeded .gdb | 78.2 C | 20.8 | 91.4 | 84.8 | 97.9 | 0 / 12 / 18 / 14 |
| seeded .gpkg | 66.6 D | 15.3 | 68.6 | 58.3 | 96.6 | 3 / 7 / 19 / 14 |

The clean data is not error-free. Esri's original sample has real findings, such as 25 Busbar voltages outside their domain, missing metadata, and 34 Disconnect, 11 PowerTransformer and 9 CircuitBreaker points that are inside stations and not on a line. Judge each seeded copy by its **difference** from its clean twin. A clean copy must never produce a critical finding.

## Catch and miss summary
| Version | Caught | Missed | Not planted |
|---|---|---|---|
| v0.3.0 | 12 | 3 (G07 orphan junction, P01 missing GlobalID field, P06 GPKG domain) | 2 |
| v0.4.0 | 14 | 1 (P06: a GeoPackage has no Esri domains, so this miss is expected) | 2 |

G02 and G04 (a duplicate and a null GlobalID appended to the FGDB) never planted, because GDAL's OpenFileGDB writer regenerates GlobalIDs. P03 and P04 cover the same cases in the GPKG. `tests/test_testdata.py` enforces these results in CI.
