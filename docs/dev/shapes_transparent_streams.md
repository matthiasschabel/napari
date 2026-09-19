# Transparent Shapes Stream Investigation

**Status:** Deferred
**Last updated:** 2026-08-16
**Scope:** Shapes face and edge triangulation, slice uploads, and rendering

## Context

Shapes stores face and edge triangles in one model mesh and submits them through
one VisPy `MeshVisual`. A fully transparent face or edge therefore still consumes
model storage and GPU upload bandwidth. This investigation tested whether
omitting zero-alpha triangles is a useful non-breaking P2 optimization.

The model mesh is not render-only. The same geometry supports 2D and 3D hit
testing, selection outlines, and color-only style reactivation. Lines and Paths
have no face mesh to substitute for their edge interaction geometry. Removing
transparent geometry during model construction would therefore change behavior
unless interaction geometry became lazy and pull-based.

At the adapter boundary, omission is unconditionally pixel-safe only for
`translucent_no_depth`. Default `translucent` rendering still enables depth
testing, and zero-alpha fragments can write depth. Opaque, additive, minimum,
and multiplicative modes also change pixels when those fragments are removed.

## Current Decision

Do not implement transparent-stream omission now.

Filtering only triangle indices improved large filled layers by 7 to 12 percent
in `translucent_no_depth`, but regressed the equivalent outline workload by 1.9
percent. The safe mode is not the default, camera-only frames do not reupload
geometry, and the candidate requires blend-mode eligibility and invalidation on
style or blending transitions. That is a limited practical return compared with
the P0/P1 gains and the remaining incremental-editing work.

Do not skip edge construction in the model. With the normal warmed Bermuda
backend, edge triangulation accounted for about 2 percent of total construction
for 4,096 non-convex 20-vertex polygons. The 42 percent figure seen in the
initial pure-Python rectangle profile was a backend- and workload-specific upper
bound, not representative napari behavior.

## Measurements

### Render filtering

Each round switches between two z slices, processes Qt events, explicitly draws
the VisPy scene, and calls `glFinish`. Baseline and candidate arms are
interleaved. Per-arm counters prove that zero-alpha triangles were dropped, and
offscreen render comparisons were byte-identical.

| Workload | Baseline median | Filter median | Effect |
|---|---:|---:|---:|
| 4,096 filled rectangles, transparent edge | 18.52 ms | 16.42 ms | 11.1% faster |
| 16,384 filled rectangles, transparent edge | 69.47 ms | 63.89 ms | 7.0% faster |
| 32,768 filled rectangles, transparent edge | 156.31 ms | 144.18 ms | 7.5% faster |
| 16,384 outline rectangles, transparent face | 68.73 ms | 72.10 ms | 1.9% slower |

The effect column uses the median of paired baseline-minus-candidate differences,
not the difference between independent arm medians. The filled 16,384-shape and
outline cases had bimodal candidate samples and worse p90 latency despite their
median results. Camera-only frames were unchanged.

Corrected follow-up cases closed two review gaps:

| Workload | Paired median effect | Pixel difference |
|---|---:|---:|
| 4,096 filled rectangles, compact triangles and vertices | 8.5% faster | 0 |
| 4,096 filled rectangles, triangle indices only | 11.1% faster | 0 |
| 4,096 overlapping filled rectangles, triangle indices only | 2.5% faster | 0 |

Vertex compaction removed the expected vertices but underperformed index-only
filtering because it adds a used-vertex mask, cumulative remap, and vertex copy.
The overlapping case confirms exact pixels under ordering and heavy fill stress,
while showing that fragment cost dominates the upload saving there.

### Construction

Plain wall-clock timings were kept separate from optional cProfile output. For
4,096 warmed 20-vertex polygons:

| Operation | Median |
|---|---:|
| Full `Shapes` construction | 157.53 ms |
| Bermuda face and edge combined | 49.43 ms |
| Bermuda face only | 45.37 ms |
| Bermuda edge only | 3.04 ms |
| Paired combined-minus-face | 3.96 ms |

Standalone edge time was 1.93 percent of layer construction. The paired
combined-minus-face estimate was 2.51 percent. Both are small enough that the
interaction-semantics redesign, not triangulation itself, dominates the
yield/effort decision.

### Environment and commands

- Measurements intentionally used integration checkout `a959d67f`, because this
  P2 gate evaluates the combined renderer after the relevant P0/P1 changes
  through `520207ed`. The probes were authored on upstream-only feature base
  `7290750c` and reproduce these exact conditions after mirroring to integration;
  the numerical results are not claimed for the upstream-only base.
- macOS 26.5.1 arm64, Apple M5, OpenGL 2.1 Metal 90.5.
- Python 3.12.12, NumPy 2.2.6, PyQt6 6.10.2, VisPy 0.16.2,
  Bermuda 0.1.7.
- Synchronous slicing and an automatically isolated temporary `NAPARI_CONFIG`.
- The editable distribution metadata reports `g7290750c`; the Git checkout
  above is the authoritative source state.

Run from the repository root:

```shell
python tools/perfmon/probe_shapes_transparent_stream_render.py \
  --mode paired --paired-candidate triangles --style fill \
  --shapes 16384 --layout grid --repeats 12
python tools/perfmon/probe_shapes_transparent_stream_construction.py \
  --count 4096 --shape polygon --backend fastest --repeats 7
```

### Raw JSON

The raw sample arrays are retained so future comparisons can detect the observed
bimodality rather than relying only on medians. The common environment block is
recorded above.

<details>
<summary>4,096 filled rectangles</summary>

```json
{"mode":"paired","style":"fill","shapes":4096,"layout":"grid","blending":"translucent_no_depth","repeats":12,"model_vertices":57344,"displayed_triangles":20480,"displayed_transparent_triangles":16384,"timings":{"baseline":{"median_ms":18.508063,"p90_ms":18.920075,"samples_ms":[22.379458,17.926791,18.555334,18.006916,18.460792,18.065708,18.748625,18.107208,18.626,18.555875,18.939125,18.269542]},"triangles":{"median_ms":16.216021,"p90_ms":17.1220833,"samples_ms":[17.168875,16.056208,16.700958,15.786083,16.532834,15.862375,16.194917,15.994083,16.237125,15.862625,17.571792,16.625416]}},"upload_by_mode":{"baseline":{"set_data_calls":24,"input_triangles":491520,"input_transparent_triangles":393216,"vertices":1376256,"triangles":491520,"dropped_triangles":0,"uploaded_transparent_triangles":393216},"triangles":{"set_data_calls":24,"input_triangles":491520,"input_transparent_triangles":393216,"vertices":1376256,"triangles":98304,"dropped_triangles":393216,"uploaded_transparent_triangles":0}},"filter_validation":{"filter_expected":true,"filter_applied":true,"valid":true},"pixel_difference":{"max_channel_difference":0,"different_channel_values":0}}
```

</details>

<details>
<summary>16,384 filled rectangles</summary>

```json
{"mode":"paired","style":"fill","shapes":16384,"layout":"grid","blending":"translucent_no_depth","repeats":12,"model_vertices":229376,"displayed_triangles":81920,"displayed_transparent_triangles":65536,"timings":{"baseline":{"median_ms":69.469083,"p90_ms":70.3576039,"samples_ms":[97.716125,70.050083,69.48075,69.032291,68.713167,69.457416,69.296167,69.661709,70.090042,69.305417,68.830709,70.387333]},"triangles":{"median_ms":63.892604,"p90_ms":88.7418041,"samples_ms":[61.838042,59.528041,89.010125,60.407,65.214708,86.326916,86.048291,61.958208,62.303209,84.579375,62.5705,90.424083]}},"upload_by_mode":{"baseline":{"set_data_calls":24,"input_triangles":1966080,"input_transparent_triangles":1572864,"vertices":5505024,"triangles":1966080,"dropped_triangles":0,"uploaded_transparent_triangles":1572864},"triangles":{"set_data_calls":24,"input_triangles":1966080,"input_transparent_triangles":1572864,"vertices":5505024,"triangles":393216,"dropped_triangles":1572864,"uploaded_transparent_triangles":0}},"filter_validation":{"filter_expected":true,"filter_applied":true,"valid":true},"pixel_difference":{"max_channel_difference":0,"different_channel_values":0}}
```

</details>

<details>
<summary>32,768 filled rectangles</summary>

```json
{"mode":"paired","style":"fill","shapes":32768,"layout":"grid","blending":"translucent_no_depth","repeats":12,"model_vertices":458752,"displayed_triangles":163840,"displayed_transparent_triangles":131072,"timings":{"baseline":{"median_ms":156.3124375,"p90_ms":157.2145711,"samples_ms":[166.660875,156.863125,133.027708,130.238041,156.52825,156.024459,133.00625,132.835333,156.639542,156.096625,157.073083,157.230292]},"triangles":{"median_ms":144.1835005,"p90_ms":145.1006125,"samples_ms":[122.0415,143.345583,143.926292,144.780333,144.440709,121.699167,144.83725,144.533667,145.129875,119.942333,122.244791,147.148042]}},"upload_by_mode":{"baseline":{"set_data_calls":24,"input_triangles":3932160,"input_transparent_triangles":3145728,"vertices":11010048,"triangles":3932160,"dropped_triangles":0,"uploaded_transparent_triangles":3145728},"triangles":{"set_data_calls":24,"input_triangles":3932160,"input_transparent_triangles":3145728,"vertices":11010048,"triangles":786432,"dropped_triangles":3145728,"uploaded_transparent_triangles":0}},"filter_validation":{"filter_expected":true,"filter_applied":true,"valid":true},"pixel_difference":{"max_channel_difference":0,"different_channel_values":0}}
```

</details>

<details>
<summary>16,384 outline rectangles</summary>

```json
{"mode":"paired","style":"outline","shapes":16384,"layout":"grid","blending":"translucent_no_depth","repeats":12,"model_vertices":229376,"displayed_triangles":81920,"displayed_transparent_triangles":16384,"timings":{"baseline":{"median_ms":68.733562,"p90_ms":71.2355256,"samples_ms":[102.594042,70.78825,71.276709,70.864875,68.925333,68.264958,68.335541,69.381291,68.455459,67.806125,68.27175,68.541791]},"triangles":{"median_ms":72.1027915,"p90_ms":93.2130577,"samples_ms":[70.928458,71.990417,98.439708,72.215166,67.473792,93.532666,90.336583,67.90225,67.531458,89.2915,67.736667,89.877208]}},"upload_by_mode":{"baseline":{"set_data_calls":24,"input_triangles":1966080,"input_transparent_triangles":393216,"vertices":5505024,"triangles":1966080,"dropped_triangles":0,"uploaded_transparent_triangles":393216},"triangles":{"set_data_calls":24,"input_triangles":1966080,"input_transparent_triangles":393216,"vertices":5505024,"triangles":1572864,"dropped_triangles":393216,"uploaded_transparent_triangles":0}},"filter_validation":{"filter_expected":true,"filter_applied":true,"valid":true},"pixel_difference":{"max_channel_difference":0,"different_channel_values":0}}
```

</details>

<details>
<summary>4,096 polygon construction</summary>

```json
{"requested_backend":"Fastest available","resolved_backend":"bermuda","shape":"polygon","count":4096,"repeats":7,"layer":{"median_s":0.1575313749,"min_s":0.1563816250,"p25_s":0.1568917915,"p75_s":0.1578022495,"samples_s":[0.1578764160,0.1563816250,0.1570982500,0.1575313749,0.1577280830,0.1578847090,0.1566853330]},"bermuda":{"combined":{"median_s":0.0494290410,"min_s":0.0489730419,"p25_s":0.0491485210,"p75_s":0.0496086670,"samples_s":[0.0498037500,0.0495022091,0.0497151250,0.0492333750,0.0489730419,0.0494290410,0.0490636670]},"face":{"median_s":0.0453742910,"min_s":0.0451063330,"p25_s":0.0451688750,"p75_s":0.0456132914,"samples_s":[0.0455713749,0.0457620000,0.0453742910,0.0452146250,0.0451231250,0.0456552079,0.0451063330]},"edge":{"median_s":0.0030418340,"min_s":0.0029774170,"p25_s":0.0029845629,"p75_s":0.0030715000,"samples_s":[0.0031511670,0.0030418340,0.0030981670,0.0029819589,0.0029774170,0.0030448331,0.0029871670]},"paired_combined_minus_face":{"median_s":0.0039573340,"min_s":0.0037402090,"p25_s":0.0038118750,"p75_s":0.0041255626,"samples_s":[0.0042323751,0.0037402090,0.0043408340,0.0040187500,0.0038499170,0.0037738330,0.0039573340]},"edge_fraction_of_layer":0.0193093851,"paired_combined_minus_face_fraction_of_layer":0.0251209258}}
```

</details>

<details>
<summary>Corrected compaction and overlap follow-ups</summary>

```json
{"candidate":"triangles","layout":"grid","shapes":4096,"baseline_samples_ms":[22.010875,18.183709,18.453917,18.786333,18.330709,18.331667,18.709708,18.5795,18.255209,18.583667,18.087875,18.757],"candidate_samples_ms":[16.829,16.138958,16.761834,16.753667,16.384958,16.083958,16.224917,16.544208,16.851584,16.20375,16.445125,16.1625],"paired_median_percent":11.0997323499,"uploaded_vertices":{"baseline":1376256,"candidate":1376256},"uploaded_triangles":{"baseline":491520,"candidate":98304},"pixel_max_difference":0}
{"candidate":"compact","layout":"grid","shapes":4096,"baseline_samples_ms":[21.6715,18.450041,18.572125,18.195667,18.540292,17.877042,18.372166,17.87675,18.65375,17.955667,18.63125,17.915667],"candidate_samples_ms":[17.573708,16.467458,16.912875,17.09925,17.01525,16.135916,17.003917,16.560375,16.684125,16.416541,17.069917,16.439292],"paired_median_percent":8.4759978637,"uploaded_vertices":{"baseline":1376256,"candidate":196608},"uploaded_triangles":{"baseline":491520,"candidate":98304},"pixel_max_difference":0}
{"candidate":"triangles","layout":"overlap","shapes":4096,"baseline_samples_ms":[180.668541,180.123125,179.115709,183.183583,181.88525,179.008666,184.03375,182.779958,175.861458,182.282125,182.196375,183.477875],"candidate_samples_ms":[178.569958,177.960792,171.134834,178.769625,178.188625,174.007584,177.97775,179.485542,175.954458,177.40725,176.058791,172.957083],"paired_median_percent":2.5419693877,"uploaded_vertices":{"baseline":1376256,"candidate":1376256},"uploaded_triangles":{"baseline":491520,"candidate":98304},"pixel_max_difference":0}
```

</details>

## Alternatives Considered

| Alternative | Disposition |
|---|---|
| Filter triangle indices in the VisPy adapter | Deferred. Moderate fill-only benefit in a non-default blend mode; outline regression and invalidation complexity. |
| Compact unused vertices as well | Rejected. Corrected paired measurement saved 8.5%, less than index-only filtering's 11.1%, while adding mask, remap, and vertex-copy work. |
| Skip transparent edge construction | Deferred. Breaks hit testing, outlines, and color reactivation without lazy interaction geometry. |
| Remove edges only from the aggregate mesh | Deferred. Breaks face/edge slices and color-update index bookkeeping. |
| Discard zero-alpha fragments in a shader | Rejected. Does not reduce upload traffic and is unsafe for several blend modes. |

## Deferred Work

Two independent prerequisites could change this decision:

1. A render-payload separation that makes stream omission cheap and keeps
   interaction geometry authoritative.
2. Lazy per-shape edge construction with pull-based hit testing and outlines.

An active-edit overlay may establish the first boundary, but does not provide
the second by itself.

## Next Steps

1. Proceed to the active-edit overlay and incremental Shapes storage item.
2. Reopen transparent-stream omission only if a representative default-mode
   path can preserve exact pixels or if render-payload separation makes the
   non-default safe-mode optimization nearly free.
3. Re-run the retained probes on Linux, Windows, and a discrete GPU before
   treating the macOS ratios as portable.
