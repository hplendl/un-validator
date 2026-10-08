# Data sources

The validator was developed and tested only with the public datasets below. **None of them is included in this
repository**; download them from the original sources if you want to reproduce the sample runs (put them under
`./data`, for example `data/foundations/...`, `data/prosdk/...`, `data/other/...`).

| Dataset | Source | Licence / terms |
|---|---|---|
| Electric Utility Network Foundation (asset packages) | https://www.arcgis.com/home/item.html?id=9ae206ab278a4cce8eb4b5c3945d81c7 | Apache-2.0 (Esri ArcGIS Solutions) |
| Water Utility Network Foundation (Essentials, Expanded) | https://www.arcgis.com/home/item.html?id=b94fc7e3d314475ab2c7867277bf5849 | Apache-2.0 |
| Sewer Utility Network Foundation | https://www.arcgis.com/home/item.html?id=b8abe33344104945b8ce187674c2ac16 | Apache-2.0 |
| Stormwater Utility Network Foundation | https://www.arcgis.com/home/item.html?id=1f8518975e90430e9df5be10c5540b99 | Apache-2.0 |
| Gas and Pipeline Referencing UN Foundation (UPDM) | https://www.arcgis.com/home/item.html?id=131f7577a8c14946bdfda5f41a5e0151 | Apache-2.0 |
| Communications Utility Network Foundation | https://www.arcgis.com/home/item.html?id=820392d5edf64acfa1ddd46079a06984 | Apache-2.0 |
| District Energy Utility Network Foundation | https://www.arcgis.com/home/item.html?id=cc908f34fb7e4809a0042629a7333e90 | Apache-2.0 |
| Electric Unbalanced Distribution Data Migration Tutorial | https://www.arcgis.com/home/item.html?id=a1ff083904ff456293e87432ea63b7a0 | Apache-2.0 |
| Electric Transmission Data Migration Tutorial | https://www.arcgis.com/home/item.html?id=89d7054c6f0b46d7aad36aa3678967d3 | Apache-2.0 |
| Water Data Migration Tutorial | https://www.arcgis.com/home/item.html?id=9876b56427ab45b2a5ca39d4bc99d292 | Apache-2.0 |
| Sewer Data Migration Tutorial | https://www.arcgis.com/home/item.html?id=bab1de4b633849b4955616afe5915cab | Apache-2.0 |
| ArcGIS Pro SDK community sample data (NapervilleElectric, NapervilleWater) | https://github.com/Esri/arcgis-pro-sdk-community-samples/releases/download/3.7.0.1901/CommunitySampleData-UtilityNetwork-04-23-2024.zip (repository: https://github.com/Esri/arcgis-pro-sdk-community-samples) | Apache-2.0 (repository) |
| Attribute rules in the ArcGIS Utility Network (tutorial project package) | https://www.arcgis.com/home/item.html?id=38d698dec2b141dba7159249ca09e237 | Tutorial use |
| City of Langley (BC) Water Utility | https://www.arcgis.com/home/item.html?id=4381f23de4f04cadbbf596366729d7e9 | Open Government License – City of Langley, https://langleycity.ca/open-data-license |
| City of SeaTac (WA) Stormwater Infrastructure | https://www.arcgis.com/home/item.html?id=72aa83f6fcf946b2b4bfa14089a04fb8 | City disclaimer: informational use, no warranty |
| City of Naperville (IL) Storm Water | https://www.arcgis.com/home/item.html?id=7b50809160ed47298533358c25b4ab0c | City of Naperville open data terms of use |

**Attribution:** Contains information licensed under the Open Government License – City of Langley.

Map tiles in the UI: © OpenStreetMap contributors (https://www.openstreetmap.org/copyright).

## Derived baseline shipped with this repository

`app/data/foundation_assets.csv` and `app/data/foundation_assets.json` list asset group and asset type names and codes extracted from the public Utility Network Foundation asset packages in the table above (electric, water, gas, sewer, stormwater, communications and district energy). They are not the source geodatabases. Those packages are Apache-2.0, copyright Esri. This table is distributed under the same licence, with this credit: *Copyright Esri. Derived from the ArcGIS Solutions Utility Network Foundation asset packages, licensed under Apache-2.0.*
