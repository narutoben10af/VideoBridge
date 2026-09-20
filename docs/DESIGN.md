# Native design direction

Status: a native toolbar/player/compact-controller implementation is prepared alongside the timing correction. Build and source checks pass; rendered UI and lifecycle acceptance remain pending. Genuine Liquid Glass is deferred with Xcode setup.

Use the restraint of QuickTime Player, the clear playback hierarchy of Apple TV, and the compact controls of Apple Music as references. Preserve VideoBridge’s own name and purpose. Do not add a library, account, artwork service or browsing sidebar without a user need.

## Main player

- Let the video or receiver state occupy the main area. Keep one transport row with ten-second back, play/pause and ten-second forward, plus an accessible timeline.
- Place a clear AirPlay control in the native toolbar. Distinguish a chosen route, active external playback, preparation and buffering. Never label Mac-only playback as TV playback.
- Keep subtitle language and Off one action away. Show import and styling-loss notices when relevant rather than permanently covering the player with implementation details.
- Empty state: Open video and browser connection guidance. Incoming video: readable title and explicit Replace, preserving the current session until chosen.
- Put diagnostics, direct source addresses and connection overrides behind a details surface. Errors remain visible with an actionable recovery button.

## Compact controller

One shared session, readable title, playback/receiver state, ten-second transport, time, subtitle selector, Stop and Open VideoBridge. Closing the main window must not end the session. Do not create duplicate players when reopening it.

## Materials and accessibility

Use genuine native Liquid Glass for appropriate control surfaces once the SDK is available; do not call a regular blur material Liquid Glass. Keep video content unobscured and controls legible. Respect reduced transparency/motion, increased contrast, keyboard focus and VoiceOver. Verify light and dark appearance, long episode names, errors, incoming offers, preparation and recovery states.

## Acceptance

Capture and inspect the actual main window and menu panel. Test opening/closing/minimizing/reopening, keyboard and pointer controls, subtitle Off/language selection, recovery and route changes. Design completion requires these checks as well as the requested native material; a source patch or screenshot mockup does not suffice.

References: [Apple materials guidance](https://developer.apple.com/design/human-interface-guidelines/materials), [toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars), [adopting Liquid Glass](https://developer.apple.com/documentation/TechnologyOverviews/adopting-liquid-glass).
