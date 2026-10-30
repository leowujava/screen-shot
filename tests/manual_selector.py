from selector import RegionSelector


def on_select(region):
    print("Selected:", region)


if __name__ == "__main__":
    RegionSelector(x=0, y=0, width=1920, height=1080, on_select=on_select).show()
