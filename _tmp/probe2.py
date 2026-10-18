import tkinter as tk

root = tk.Tk()
root.geometry("1000x300+0+0")
f = tk.Frame(root, width=996, height=60)
f.pack()
f.grid_propagate(False)
for i in range(3):
    f.grid_columnconfigure(i, weight=1, uniform="u")
f.grid_columnconfigure(3, weight=0, minsize=0)
btns = []
padxs = [(0,5),(5,5),(5,0)]
for i in range(3):
    b = tk.Button(f, text=f"b{i}")
    b.grid(row=0, column=i, padx=padxs[i], sticky="ew")
    btns.append(b)
f.grid_rowconfigure(0, minsize=52)

def report(tag):
    root.update_idletasks(); root.update()
    gw = [f.grid_size, ]
    widths = [b.winfo_width() for b in btns]
    xs = [b.winfo_x() for b in btns]
    print(tag, "frame w:", f.winfo_width(), "widths:", widths, "xs:", xs)

report("minsize0")
f.grid_columnconfigure(3, minsize=2)
report("minsize2")
# also test: does remainder go to first col? try frame width 997 (usable 987 -> rem 0? 987%3=0)
f.configure(width=997); report("w997 m2")
f.configure(width=998); report("w998 m2")
root.destroy()
