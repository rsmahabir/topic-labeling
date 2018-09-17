import pandas as pd
from tabulate import tabulate


def tprint(df: pd.DataFrame, head=10, to_latex=False):
    if head > 0:
        df = df.head(head)
    elif head < 0:
        df = df.tail(-head)
    print(tabulate(df, headers="keys", tablefmt="pipe") + '\n')

    if to_latex:
        print(df.to_latex(bold_rows=True))


def hms_string(sec_elapsed):
    h = int(sec_elapsed / (60 * 60))
    m = int((sec_elapsed % (60 * 60)) / 60)
    s = sec_elapsed % 60
    return "{}:{:>02}:{:>05.2f}".format(h, m, s)