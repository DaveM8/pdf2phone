# pdf2phone library organisation: goals

Date: 2026-10-21

## Status

Draft, not signed off. No code until this says "Signed off <date>".
The owner is thinking this over for a few days; questions are answered one at a time.

## Problem

The library is one flat list, newest first. Each item shows the title from the
PDF (or its first heading), the date and the original filename. Items can't be
renamed or grouped. As the owner adds more articles and books, all of these
are or will be true:

- a book is hard to find in a long list
- titles are long or meaningless (taken from filenames or bad PDF metadata)
- documents on different subjects are mixed together

## Goal

On my phone I can find any book or article in a few taps, because each one
has a name I chose, sits in a folder I made, and can be found by searching.

## Success criteria

- [ ] 1. From the ⋯ menu I can give a book a new name. The new name shows in the list and at the top of the reader, and my reading position is kept.
- [ ] 2. I can make a folder, move a book into it, and opening the folder shows only the books inside.
- [ ] 3. Typing part of a name into a search box shows the matching books as I type.
- [ ] 4. With 100 books in the library, I can open any one of them in under 10 seconds.
- [ ] 5. All of it works on the iPhone with big text, and nothing makes the page scroll sideways.

## Non-goals

- Searching inside the text of books (search matches names only)
- Sharing books or folders with other users (everything stays private)
- Tags (decided 2026-10-28; could come in a later version)

## Constraints

- A book's id (from its filename) is its web address and the key for its saved reading position. Renaming or moving a book must never change the id.
- Each user's books, names and folders are private. Deleting an account erases all of them (tests/test_delete.py must keep passing).
- Phone first: big text, OpenDyslexic, no sideways scrolling; horizontal spacing stays in px.
- Same Hetzner server, Docker setup and deploy steps.
- No deadline: ship when it's right.

## Risks and irreversible steps

- Existing books and reading positions could be lost if storage changes. Protection: book folders on disk are never moved or changed. Names and folders are stored separately, so there is nothing to migrate, and removing the feature would leave the books as they were. (chosen 2026-10-28)
- Server backups (GOALS.md milestone 4) aren't built yet, so there's no safety net for mistakes.
- Deleting a folder can't be undone. Protection: only empty folders can be deleted, so no book is ever deleted along with a folder.

## Decisions

| Decision | Chosen | Alternatives | Why |
|---|---|---|---|
| Grouping | Folders inside folders; each book in one place | Tags only; both; one level of folders | Works like the Files app; simple on a phone |
| Deleting a folder | Only when it's empty | Move contents up; delete everything inside | Nothing can be lost by accident |
| Naming new books | Optional "Name" box on the upload form; empty means the PDF's title; rename later from ⋯ | Automatic only; name required | Good names from the start without an extra step |
| Where new books go | Into the folder you're in; the upload form appears in every folder | Folder drop-down; always top level | No moving books by hand afterwards |
| What search looks through | Whole library, books and folders, from any folder; results show their folder path | This folder and below; books only | No need to remember where a book was put |
| Order inside a folder | Folders A–Z first, then books newest first | Everything A–Z; recently read first; a sort button | Recent additions stay easy to reach |

## Open questions

- [x] Folders inside folders or tags? Folders inside folders only; tags left out. (decided 2026-10-28)
- [ ] Where names and folders are stored: the existing SQLite database, or a small file in each user's folder. (decide by: milestone 1)
- [ ] Do names and folders also apply to the offline CLI library (`pdf2phone convert`), or only the web app? (decide by: milestone 1)
- [ ] How a book is moved: a "Move to…" item in the ⋯ menu with a folder list, or something else. Can folders be renamed and moved too? (decide by: milestone 2)
- [ ] Does search match only the chosen name, or also the PDF's own title and the original filename? (decide by: milestone 3)

## Milestones

### Milestone 1: names (first)

- [ ] "Rename" in the ⋯ menu; the new name shows in the list and at the top of the reader
- [ ] Optional "Name" box on the upload form; empty means the PDF's title
- [ ] The book's id, web address and reading position don't change

Done when: check 1 passes on the iPhone, with big text and no sideways scrolling (check 5).

### Milestone 2: folders

Folders inside folders, moving books, upload into the folder you're in,
delete only empty folders, order as decided. Done when check 2 passes.

### Milestone 3: search

Search box over the whole library, as decided. Done when checks 3 and 4 pass.
