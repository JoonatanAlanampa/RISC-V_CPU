/* hello.c — the iconic milestone: the CPU speaks.
 *
 * putc busy-polls the UART status (a LOAD from I/O space), then writes the
 * character (a STORE to I/O space). putdec uses % and / — RV32I has no
 * divide, so those become libgcc's software __umodsi3/__udivsi3, running
 * on this core like any other code.
 */

#define UART_REG (*(volatile unsigned *)0x10004)
#define LED_REG  (*(volatile unsigned char *)0x10000)
#define QSPI_CFG (*(volatile unsigned *)0x1000C)

static void putc1(char c)
{
    while (UART_REG & 1)
        ;                           /* wait while transmitter busy */
    UART_REG = (unsigned char)c;
}

static void puts1(const char *s)
{
    while (*s)
        putc1(*s++);
}

static void putdec(unsigned v)
{
    char buf[10];
    int i = 0;
    do {
        buf[i++] = (char)('0' + v % 10);
        v /= 10;
    } while (v);
    while (i--)
        putc1(buf[i]);
}

__attribute__((noinline))
static int fib(int n)
{
    return n < 2 ? n : fib(n - 1) + fib(n - 2);
}

int main(void)
{
    QSPI_CFG = 3;   /* boot was serial (safe); go QUAD for the show */
    puts1("Hello from my own CPU!\n");
    unsigned f = (unsigned)fib(10);
    puts1("fib(10)=");
    putdec(f);
    putc1('\n');
    LED_REG = (unsigned char)f;     /* 55 = 00110111 on the LEDs */
    return (int)f;
}
