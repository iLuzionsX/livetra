# HIG findings for the operator settings menu

Sources researched:

- Apple HIG: Layout and organization - https://developer.apple.com/design/human-interface-guidelines/layout-and-organization
- Apple HIG: Menus and actions - https://developer.apple.com/design/human-interface-guidelines/menus-and-actions
- Apple HIG: Presentation - https://developer.apple.com/design/human-interface-guidelines/presentation
- Related indexed Apple HIG pages used for readable guidance: Layout, Menus, and Sheets.

## Findings

1. Group related controls and make the grouping visible.
   The settings panel should separate caption setup, input/caption display, live session actions, export actions, and advanced timing. These groups should be scannable without reading every control.

2. Keep the most important and frequent actions first.
   Live operation depends most on Start/Stop, Pause/Resume, language direction, microphone selection, and projector access. Export formats and timing internals are secondary.

3. Use controls that match the job.
   Source language, target language, and microphone selection are finite choices, so select menus are a better fit than freeform inputs. Boolean options should remain checkboxes. Numeric timing values belong in explicit number inputs or sliders.

4. Keep menus short and split long sets into logical groups.
   A single mixed settings menu makes operators scan too much while audio is live. Split secondary actions such as export and advanced timing into their own groups or disclosures.

5. Use progressive disclosure for rarely changed controls.
   Custom vocabulary and advanced timing affect behavior but are not the usual live path. They should stay available without competing with primary caption setup.

6. Prefer nonmodal controls for settings that affect the current task.
   The operator needs to adjust language direction, mic input, caption polish, and projector display while watching the transcript. An inline settings panel is appropriate because it preserves the live context.

7. Preserve clear visual hierarchy and alignment.
   Labels, controls, and action rows should align across columns. The reading order should put caption direction first, then input and display, then live actions, exports, and advanced details.

## LiveTR3 implementation rules

- Keep Start/Stop and Pause/Resume in the persistent header.
- Put source and target language controls at the top of the settings panel.
- Use select menus for language choices and mic selection.
- Group language swap with caption setup because it changes caption direction.
- Put projector access with caption display controls.
- Put Commit Now and Skip Next Polish together as live session actions.
- Put TXT, SRT, and VTT export buttons together.
- Keep Custom vocabulary and Advanced timing behind disclosures.
