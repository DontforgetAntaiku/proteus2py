CC = i686-w64-mingw32-gcc
CFLAGS = -O2 -Wall -Wextra -std=gnu11
all: p2hook.dll p2launch.exe
p2hook.dll: hook.c
	$(CC) $(CFLAGS) -shared -static-libgcc -o $@ hook.c
p2launch.exe: launch.c
	$(CC) $(CFLAGS) -static-libgcc -o $@ launch.c
clean:
	rm -f *.dll *.exe

# put the build into the Python package so `python -m build` ships it in the wheel (CI does this before building)
package: all
	mkdir -p ../src/proteus2py/_native
	cp p2hook.dll p2launch.exe ../src/proteus2py/_native/
