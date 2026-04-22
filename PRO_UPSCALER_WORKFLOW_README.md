# Pro Upscaler Workflow README

This document describes the client-adjustable parameters for the Pro Upscaler workflow and how those parameters should be applied to the ComfyUI API JSON workflow.

It is intended for backend developers implementing the workflow on a new website.

## Scope

The Pro Upscaler workflow is controlled by a small set of website inputs:

- Direct node parameters: input image, upscale value, creativity, and generated random seed values update specific ComfyUI node fields.
- Routing parameters: engine choice and enhancement toggle change which workflow path is connected to the final output.

The current backend implementation is in `server_upscaler_with_flux_enhancement.py`.

The current default workflow file is `api_workflow/Seedvr_flux_upscaler_03.json`.

The current workflow profile name is `Pro Upscaler`.

## Client Parameters

| UI Label | API Field | Type | Default | Range / Values | Purpose |
|---|---|---:|---:|---|---|
| Input Image | `image_b64` | string | required | base64 image | Main image uploaded by the user. |
| Engine Choice | `engine_choice` | string | `Normal` | `Super Fast`, `Normal` | Selects the processing route. |
| Upscale Value | `upscale_value` | string | `x2` | `x2`, `x4` | Controls the final upscale multiplier. |
| Enhancement | `enhancement` | boolean | `true` | `true`, `false` | Enables the Flux enhancement pass in Normal mode. |
| Creativity | `flux_creativity_tilet` | number | `30` | `10` to `40`, step `5` | Controls the workflow tile/detail creativity value. |
| Workflow Debug | `workflow_debug` | boolean | `false` | `true`, `false` | Admin-only backend option to save the final workflow JSON. |
| Workflow Profile | `workflow` | string | `Pro Upscaler` | configured workflow name | Backend workflow/profile selector. |

## Website Visibility Rules

| Client Setting | Expected UI Behavior |
|---|---|
| `engine_choice = "Normal"` | Show `enhancement` checkbox and default it to `true`. |
| `engine_choice = "Super Fast"` | Hide `enhancement` checkbox and set it to `false`. |

Important: in `Super Fast` mode, the backend does not use the `enhancement` value. The Super Fast route always bypasses the SeedVR and Flux enhancement path.

## Direct ComfyUI Node Mapping

| Client / Backend Value | API Field | ComfyUI Node ID | Node Class / Title | JSON Field Updated | Expected Behavior |
|---|---|---:|---|---|---|
| Input Image Name | internal | `99` | `LoadImage` / Load Image | `inputs.image` | Set to the placeholder image name, currently `main_image_name`. |
| Input Image Data | `image_b64` | payload image list | RunPod payload | `input.images[]` | Send base64 image data with the same name used by node `99`. |
| Creativity | `flux_creativity_tilet` | `80:84` | `PrimitiveInt` / Int | `inputs.value` | Controls the Flux enhancement creativity/tile value. |
| Flux Noise Seed | generated | `80:29` | `RandomNoise` | `inputs.noise_seed` | Backend-generated random seed for the Flux enhancement path. |
| Super Fast x2/x4 Scale | `upscale_value` | `104` | `ImageScaleBy` / Upscale Image By | `inputs.scale_by` | Used only when `engine_choice = "Super Fast"`. |
| Normal x2/x4 Scale | `upscale_value` | `96:85` | `ImageScaleBy` / Upscale Image By | `inputs.scale_by` | Used only when `engine_choice = "Normal"`. |

## Input Image Payload Format

The workflow does not place the full base64 image directly into node `99`. Instead:

1. Node `99.inputs.image` is set to a placeholder filename.
2. The same placeholder filename is included in the RunPod payload image list.

Example:

```json
{
  "99": {
    "inputs": {
      "image": "main_image_name"
    }
  }
}
```

Final RunPod payload shape:

```json
{
  "input": {
    "workflow": {},
    "images": [
      {
        "name": "main_image_name",
        "image": "base64-image-data"
      }
    ]
  }
}
```

## Scale Behavior

The meaning of `upscale_value` depends on the selected engine.

| Engine Choice | `upscale_value` | Node Updated | Value Applied | Meaning |
|---|---|---:|---:|---|
| `Super Fast` | `x2` | `104.inputs.scale_by` | `0.5` | The model upscale path already upsizes first, then this route scales down to produce x2. |
| `Super Fast` | `x4` | `104.inputs.scale_by` | `1` | Keep the model upscale output at full x4. |
| `Normal` | `x2` | `96:85.inputs.scale_by` | `2` | SeedVR/normal route targets x2. |
| `Normal` | `x4` | `96:85.inputs.scale_by` | `4` | SeedVR/normal route targets x4. |

## Connection Rule Format

In ComfyUI API JSON, node links are stored on the target node input.

For example:

```json
{
  "97": {
    "inputs": {
      "images": ["81:13", 0]
    }
  }
}
```

This means:

```text
97.images <- 81:13
```

The tables below use the same shorthand:

```text
target_node.input <- source_node
```

## Engine and Enhancement Routing Matrix

| Case | `engine_choice` | `enhancement` | `upscale_value` | Workflow Path |
|---:|---|---:|---|---|
| 1 | `Super Fast` | ignored / `false` | `x2` | Super Fast model upscale, scaled to x2, save node `104`. |
| 2 | `Super Fast` | ignored / `false` | `x4` | Super Fast model upscale, save node `104`. |
| 3 | `Normal` | `false` | `x2` | SeedVR upscale only, x2, bypass Flux enhancement. |
| 4 | `Normal` | `false` | `x4` | SeedVR upscale only, x4, bypass Flux enhancement. |
| 5 | `Normal` | `true` | `x2` | SeedVR upscale plus Flux enhancement, x2. |
| 6 | `Normal` | `true` | `x4` | SeedVR upscale plus Flux enhancement, x4. |

## Detailed Connection Behavior

### Case 1: Super Fast, x2

Client input:

```json
{
  "engine_choice": "Super Fast",
  "upscale_value": "x2"
}
```

Connections and values:

```text
102.image <- 99
97.images <- 104
104.scale_by = 0.5
```

Expected result:

- Send the input image to the Super Fast model upscale path.
- Scale the model result to x2.
- Save output from node `104`.
- Ignore the `enhancement` checkbox.

### Case 2: Super Fast, x4

Client input:

```json
{
  "engine_choice": "Super Fast",
  "upscale_value": "x4"
}
```

Connections and values:

```text
102.image <- 99
97.images <- 104
104.scale_by = 1
```

Expected result:

- Send the input image to the Super Fast model upscale path.
- Keep the model result at x4.
- Save output from node `104`.
- Ignore the `enhancement` checkbox.

### Case 3: Normal, Enhancement Off, x2

Client input:

```json
{
  "engine_choice": "Normal",
  "enhancement": false,
  "upscale_value": "x2"
}
```

Connections and values:

```text
96:82.image <- 99
97.images <- 81:13
96:85.scale_by = 2
81:38.image <- 77:78
```

Expected result:

- Send the input image into the Normal preparation route.
- Upscale through SeedVR.
- Bypass Flux enhancement.
- Send SeedVR output directly into node `81:38`.
- Save final untiled output from node `81:13`.

### Case 4: Normal, Enhancement Off, x4

Client input:

```json
{
  "engine_choice": "Normal",
  "enhancement": false,
  "upscale_value": "x4"
}
```

Connections and values:

```text
96:82.image <- 99
97.images <- 81:13
96:85.scale_by = 4
81:38.image <- 77:78
```

Expected result:

- Send the input image into the Normal preparation route.
- Upscale through SeedVR.
- Bypass Flux enhancement.
- Save final untiled x4 output from node `81:13`.

### Case 5: Normal, Enhancement On, x2

Client input:

```json
{
  "engine_choice": "Normal",
  "enhancement": true,
  "upscale_value": "x2"
}
```

Connections and values:

```text
96:82.image <- 99
97.images <- 81:13
96:85.scale_by = 2
80:83.image <- 77:78
81:38.image <- 80:14
```

Expected result:

- Send the input image into the Normal preparation route.
- Upscale through SeedVR.
- Feed SeedVR output into the Flux enhancement path through node `80:83`.
- Feed enhanced batch output from node `80:14` into node `81:38`.
- Save final untiled output from node `81:13`.

### Case 6: Normal, Enhancement On, x4

Client input:

```json
{
  "engine_choice": "Normal",
  "enhancement": true,
  "upscale_value": "x4"
}
```

Connections and values:

```text
96:82.image <- 99
97.images <- 81:13
96:85.scale_by = 4
80:83.image <- 77:78
81:38.image <- 80:14
```

Expected result:

- Send the input image into the Normal preparation route.
- Upscale through SeedVR.
- Feed SeedVR output into the Flux enhancement path.
- Save final enhanced x4 output from node `81:13`.

## Node Reference

| Node ID | Node Class / Title | Workflow Role |
|---:|---|---|
| `77:78` | `SeedVR2VideoUpscaler` | Main SeedVR upscale node for Normal mode. |
| `80:12` | `SamplerCustomAdvanced` | Flux enhancement sampler. Used for progress tracking as the enhancement node. |
| `80:14` | `ImageListToBatch+` | Converts enhanced image list back into a batch. |
| `80:29` | `RandomNoise` | Flux enhancement random noise seed. |
| `80:83` | `ImageBatchToList+` | Sends SeedVR output into the Flux enhancement image list path. |
| `80:84` | `PrimitiveInt` | Creativity value input. |
| `81:13` | `ImageUntile+` | Reassembles tiled output into final image. |
| `81:38` | `ImageResize+` | Final resize before untile; source changes based on enhancement toggle. |
| `96:82` | `ImageResize+` | Normal mode input preparation resize. |
| `96:85` | `ImageScaleBy` | Normal mode upscale multiplier. |
| `96:89` | `ImageResize+` | Normal mode tile input max size clamp. |
| `96:96` | `PrimitiveInt` | Tile divisor used for workload estimation. |
| `97` | `SaveImage` | Final output node. |
| `99` | `LoadImage` | Main input image loader. |
| `102` | `ImageUpscaleWithModel` | Super Fast model upscale node. |
| `103` | `UpscaleModelLoader` | Super Fast upscale model loader. |
| `104` | `ImageScaleBy` | Super Fast final scale adjustment. |

## Workflow Profile / Progress Tracking

The `Pro Upscaler` workflow profile is used by the backend progress tracker.

Current profile values:

| Profile Field | Value | Purpose |
|---|---|---|
| `upscale_node_id` | `77:78` | Tracks SeedVR upscale progress. |
| `enhancement_node_id` | `80:12` | Tracks Flux enhancement progress. |
| `wrap_up_node_ids` | `80:14`, `81:38`, `81:13`, `97` | Tracks final wrap-up progress. |
| `seedvr_runtime_enabled` | `true` | Enables SeedVR-specific runtime progress handling. |
| `upscale_label` | `SeedVR Upscaling` | UI/progress label. |
| `enhancement_label` | `Enhancement` | UI/progress label. |

## Recommended API Payload

The new website can send a payload similar to this:

```json
{
  "image_b64": "base64-image-data",
  "engine_choice": "Normal",
  "upscale_value": "x2",
  "enhancement": true,
  "flux_creativity_tilet": 30,
  "workflow_debug": false,
  "workflow": "Pro Upscaler"
}
```

## Backend Implementation Checklist

1. Load the base ComfyUI API workflow JSON.
2. Convert the uploaded input image to base64.
3. Set `99.inputs.image` to the placeholder filename, currently `main_image_name`.
4. Add `{ "name": "main_image_name", "image": image_b64 }` to the final RunPod payload `input.images` array.
5. Generate a fresh random seed for `80:29.inputs.noise_seed`.
6. Set `80:84.inputs.value` from `flux_creativity_tilet`.
7. Apply engine routing:
   - `Super Fast`: route node `99` to node `102`, route final output to node `104`.
   - `Normal`: route node `99` to node `96:82`, route final output to node `81:13`.
8. Apply upscale value:
   - `Super Fast x2`: set `104.inputs.scale_by = 0.5`.
   - `Super Fast x4`: set `104.inputs.scale_by = 1`.
   - `Normal x2`: set `96:85.inputs.scale_by = 2`.
   - `Normal x4`: set `96:85.inputs.scale_by = 4`.
9. Apply enhancement routing only for Normal mode:
   - Enhancement on: set `80:83.inputs.image = ["77:78", 0]` and `81:38.inputs.image = ["80:14", 0]`.
   - Enhancement off: set `81:38.inputs.image = ["77:78", 0]`.
10. If `workflow_debug` is enabled for an admin user, save the final workflow JSON for debugging.
11. Submit the final payload to RunPod or ComfyUI.

## Backend Pseudocode

```python
main_image_name = "main_image_name"

prompt["99"]["inputs"]["image"] = main_image_name
prompt["80:29"]["inputs"]["noise_seed"] = random.randint(0, 999_999_999_999)
prompt["80:84"]["inputs"]["value"] = float(flux_creativity_tilet)

if engine_choice == "Super Fast":
    prompt["102"]["inputs"]["image"] = ["99", 0]
    prompt["97"]["inputs"]["images"] = ["104", 0]
    prompt["104"]["inputs"]["scale_by"] = 0.5 if upscale_value == "x2" else 1
else:
    prompt["96:82"]["inputs"]["image"] = ["99", 0]
    prompt["97"]["inputs"]["images"] = ["81:13", 0]
    prompt["96:85"]["inputs"]["scale_by"] = 2 if upscale_value == "x2" else 4

    if enhancement:
        prompt["81:38"]["inputs"]["image"] = ["80:14", 0]
        prompt["80:83"]["inputs"]["image"] = ["77:78", 0]
    else:
        prompt["81:38"]["inputs"]["image"] = ["77:78", 0]

payload = {
    "input": {
        "workflow": prompt,
        "images": [
            {
                "name": main_image_name,
                "image": image_b64,
            }
        ],
    }
}
```

## Validation Notes

The backend should validate incoming values before mutating the workflow.

Recommended validation:

| Field | Validation |
|---|---|
| `image_b64` | Required non-empty string. |
| `engine_choice` | Must be `Super Fast` or `Normal`; default to `Normal` if missing. |
| `upscale_value` | Must be `x2` or `x4`; default to `x2` if missing. |
| `enhancement` | Coerce to boolean. Ignore in Super Fast mode. |
| `flux_creativity_tilet` | Clamp to `10` through `40`; recommended step is `5`. |
| `workflow_debug` | Coerce to boolean and allow only for admin users. |

## Important Notes

- `engine_choice` controls the main workflow route.
- `enhancement` only affects Normal mode.
- In Super Fast mode, the final saved image comes from node `104`.
- In Normal mode, the final saved image comes from node `81:13`.
- In Normal mode with enhancement enabled, SeedVR output from node `77:78` is sent into the Flux enhancement path through node `80:83`.
- In Normal mode with enhancement disabled, node `81:38` receives SeedVR output directly from node `77:78`.
- `upscale_value` maps to different scale nodes depending on the selected engine.
- The final output node is always `97`, but its `images` input changes depending on the selected engine.
