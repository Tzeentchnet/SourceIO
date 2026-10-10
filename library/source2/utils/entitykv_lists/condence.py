from pathlib import Path


def condense(directory: Path = Path(".")) -> None:
    output_path = directory / "condenced.txt"
    new_keys = set()
    for file in directory.glob("*.txt"):
        if file == output_path:
            continue
        with file.open("r", encoding="utf-8-sig") as source:
            for line in source:
                new_keys.add(line.rstrip("\n"))

    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for key in sorted(new_keys):
            output.write(key + "\n")


if __name__ == "__main__":
    condense()
