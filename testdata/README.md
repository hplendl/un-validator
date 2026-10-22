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

## Expected scores (v0.3.0, reference run 2026-10-22)
| Dataset | Overall | Metadata | Lineage | Schema | Quality | Error/Warn/Info |
|---|---|---|---|---|---|---|
| clean .gdb | 75.1 C | 5.0 | 91.4 | 84.9 | 98.0 | 8 / 14 / 12 |
| clean .gpkg | 67.7 D | 0.0 | 91.4 | 58.3 | 98.5 | 4 / 11 / 7 |
| seeded .gdb | 75.1 C | 5.0 | 91.4 | 84.8 | 97.9 | 12 / 15 / 14 |
| seeded .gpkg | 62.9 D | 0.0 | 68.6 | 58.3 | 95.1 | 8 / 16 / 14 |

The clean data is not error-free. The original Esri sample already has real findings, such as 25 Busbar voltage values outside NominalVoltage and missing metadata. That is the baseline. Judge the seeded copies by the **difference** from their clean twin. (The clean gdb lineage score was 91.4 without the match-table CSVs next to it and 82.4 with them.)

## Catch and miss summary (v0.3.0)
Of the 17 planted errors, **12 were caught, 3 were missed and 2 did not plant** (the OpenFileGDB writer regenerates GlobalIDs on append):
- **Missed G07:** an orphan junction point with no line touching it. There is no point-on-line connectivity check.
- **Missed P01:** the GlobalID column was dropped from a GeoPackage class. GPKG classes are not compared with the Foundation model.
- **Missed P06:** bad coded values in a GeoPackage. There are no domains to check against, so this miss is expected.
- **Not planted G02 and G04:** a duplicate GlobalID and a NULL GlobalID in the FGDB. P03 and P04 cover the same cases in the GPKG.
- **Note:** the wrong-CRS layer (P02) was caught, but it also inflated dangles to 1,438 because cross-CRS snapping is not reprojected.
