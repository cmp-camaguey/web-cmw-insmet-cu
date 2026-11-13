# Spec — 008-exportar-graficos (capability: graphic-export)

Requirements for exporting the Skew-T + hodograph figure as PNG / PDF / SVG.
The figure is built by the external model API (`https://modelo.cmw.insmet.cu/api/sounding/`);
the portal only forwards the request and serves the bytes. Implementation:
`SoundingExportView` and `fetch_sounding_plot` in `apps/home/views/modelos/views.py`.
RFC2119 keywords (MUST, SHOULD, MAY) are used below.

## Requirement: GRAPHIC-EXPORT-FORMATS

The system SHALL request the figure in PNG, PDF, and SVG from the external sounding API,
forwarding the requested `fmt`.

### Scenario: Supported format
- **Given** a GET to `models_sounding_export` with `fmt` in `{png, pdf, svg}`
- **When** `SoundingExportView` executes
- **Then** it MUST forward `fmt` to the external API and serve the returned bytes.

### Scenario: Unsupported format
- **Given** a GET to `models_sounding_export` with `fmt` outside `{png, pdf, svg}`
- **When** `SoundingExportView` executes
- **Then** the response MUST be HTTP 400 and the external API MUST NOT be called.

## Requirement: GRAPHIC-EXPORT-DOWNLOAD

The system SHALL expose an HTTP endpoint that streams the figure bytes as a download.

### Scenario: Supported format requested
- **Given** the external API returns the figure
- **When** `SoundingExportView` builds the response
- **Then** the response MUST have `Content-Type` `image/png` | `application/pdf` |
  `image/svg+xml` and a `Content-Disposition: attachment; filename="sounding_<stamp>.<ext>"`
  header.

### Scenario: Upstream sounding API failure
- **Given** the external `modelo.cmw.insmet.cu/api/sounding/` call fails
- **When** `SoundingExportView` executes
- **Then** the response MUST be a non-2xx error (502 on connection or invalid payload, 404 when
  the API has no data) and MUST NOT return a corrupt or empty file.

## Requirement: GRAPHIC-EXPORT-INPAGE

The in-page Skew-T view MUST keep the JSON contract consumed by `static/dist/js/sounding.js`.

### Scenario: In-page JSON contract preserved
- **Given** `SoundingView.post` fetches the figure from the external API
- **When** the response is built
- **Then** the JSON MUST include `plot_image` as a base64 PNG string, plus `datetime` and
  `params`, whether the API answered the image raw (`image/*`, `application/pdf`) or as a JSON
  envelope with `plot_image` / `image` in base64 or data URI.
