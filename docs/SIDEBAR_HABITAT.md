# Dreamy desert companion perches

Updated September 24, 2026. Frontend/template presentation using the existing Flask/Jinja and vanilla JavaScript style.

## Accepted design

The user chose cozy perches after reviewing the cliff-climbing experiment. Fox stays near the top of the left sidebar; Changing Woman stays near the bottom. Neither crosses navigation or climbs the menu. Rounded sandstone shelves, sand, a sparse cactus, violet shadows and dusty-rose highlights suggest the Southwest Apachería landscape. Navigation regains its full width.

A breeze stirs Fox's fur and tail tip. It begins around 18 seconds after the page clock starts, lasts 12.8 seconds and recurs every two minutes. Changing Woman now follows the decorative sky: dawn water preparation, daytime plant-fiber work, dusk food sorting and night rest; hair and clothing shift subtly with those poses. Registered frames crossfade over 1.35 seconds. Bodies and positions remain seated. Quiet desk, Still perches, reduced motion, typing, scrolling, open dialogs and bubble interaction hold a paused activity frame. Narrow layouts hide companions; hidden pages stop the timer. No animation-frame loop or network request is used. See MOONLIT_DESK.md for historical scope and sources.

Fox / Changing Woman / Both, speech and motion preferences remain local. Old roaming preferences migrate to cozy perches. Both is the default for pre-habitat preferences; later selections persist. Bubbles provide section/field guidance and original humorous, composed lines. These are not quotations from the user's mother or Apache religious teachings.

## Scene rotation

Scenery and gentle motion default on. A decorative sky cycle lasts six minutes; landscape treatments rotate every two minutes. Turning either control off is remembered. Selecting a specific sky or treatment stops rotation. Quiet desk, reduced motion and hidden pages pause the cycle without a resume jump. The live market clock remains actual Eastern time.

## Cultural context

The user identified an Aravaipa Apache family connection and their mother as the subject of Donald Crowley's The Transformation. Her optional portrait-inspired companion is an artistic interpretation. The scene expresses that personal direction; it does not claim tribal authorship, an authentic sacred figure or an ethnographic reconstruction.

The original stepped border is informed by documented terraced Apache basket rims in the [Eddie Basha Collection](https://eddiebashacollection.com/collection/apache-basket). It is newly drawn geometry, not a reproduction of a specific basket or a claimed ceremonial emblem. The [San Carlos Apache Cultural Museum](https://www.scat-nsn.gov/cultural-museum/) is linked as a community resource; its page timed out on the final direct fetch.

The [Mescalero Apache Tribe's own account](https://mescaleroapachetribe.com/our-culture/) describes its sacred mountains, White Painted Woman, ceremonies and respect for elders. This is explicitly a Mescalero source, not authority for Aravaipa practices. Religious accounts retain community attribution; no ceremony, prayer or religious power is simulated through trading results. No verified Aravaipa mapping was found for the displayed astronomical patterns.

## Actual star patterns

static/constellations.js pins 19 bright stars queried from SIMBAD TAP on September 23, 2026, using ident joined to basic and ICRS/J2000 RA/Dec degrees rounded to six decimals. Gnomonic projection preserves proportions within each pattern, north up and east left. Charts are independently arranged, not the current local sky.

- Orion, seven stars: [NASA](https://science.nasa.gov/asset/hubble/orion-constellation/).
- Cassiopeia, five stars: [NASA/JPL](https://www.jpl.nasa.gov/images/pia15256-a-royal-celebration/).
- Big Dipper, seven stars, an asterism within Ursa Major: [NASA](https://science.nasa.gov/solar-system/what-are-asterisms/).
- Coordinates: [SIMBAD](https://simbad.cds.unistra.fr/simbad/), via its /simbad/sim-tap/sync endpoint.

The sky guide labels patterns and sources. Trade journal map markers represent recorded executions, not astronomical stars.

## Validation and boundaries

The avatar controller harness exercises stable positions through task changes, finite breeze, pause/dock/reduced motion, preference migration, hidden/narrow lifecycle and rejected network/animation-frame use. The constellation test checks coordinates/edges, bounds, north/east orientation, equal scale and Orion's belt geometry. Existing scene, companion and workspace tests cover integration.

Browser checks cover desktop/mobile layouts, actual rendered sprites, local appearance controls and Quiet desk. No trading configuration or order action is used as a test. These checks concern presentation, not trading readiness or profitability. No backend restart is needed. See SIDEBAR_HABITAT_ASSETS.md for the Fox/breeze asset and CHANGING_WOMAN_DAY_ROUTINE_ASSET.md for the current woman's activity atlas and exact prompts.
