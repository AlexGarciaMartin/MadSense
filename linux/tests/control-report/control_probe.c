/* Fixed reports to an attributed virtual Sony device in an owned QEMU guest.
 * Does not detach drivers, reset devices, change quirks or access physical USB.
 * API return values are observations; the runner separately correlates the
 * copied actor's pre-normalization request, raw output and synthetic BT output.
 */
#define _XOPEN_SOURCE 700
#include <fcntl.h>
#include <inttypes.h>
#include <linux/hidraw.h>
#include <linux/input.h>
#include <linux/usbdevice_fs.h>
#include <stdint.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <sys/sysmacros.h>
#include <time.h>
#include "../../research/consumer_guard.h"

static uint64_t now_ns(void)
{
    struct timespec t;
    return clock_gettime(CLOCK_MONOTONIC, &t) == 0
        ? (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec : 0;
}
static bool number_file(const char *parent, const char *name, unsigned *value)
{
    char path[PATH_MAX], tail;
    int count = snprintf(path, sizeof(path), "%s/%s", parent, name);
    if (count < 0 || (size_t)count >= sizeof(path)) return false;
    FILE *f = fopen(path, "r"); if (!f) return false;
    bool ok = fscanf(f, "%u %c", value, &tail) == 1; fclose(f); return ok;
}
static int exact_open(const char *path)
{
    struct stat before, after;
    if (lstat(path, &before) || !S_ISCHR(before.st_mode) || before.st_uid != 0) { errno = EPERM; return -1; }
    int fd = open(path, O_RDWR | O_CLOEXEC | O_NONBLOCK | O_NOFOLLOW);
    if (fd < 0) return -1;
    if (fstat(fd, &after) || before.st_dev != after.st_dev || before.st_ino != after.st_ino
        || before.st_rdev != after.st_rdev || before.st_uid != after.st_uid) {
        close(fd); errno = ESTALE; return -1;
    }
    return fd;
}
static bool exact_usbfs(const char *parent, const char *usbfs)
{
    unsigned bus, device; char expected[80], link[80], actual[PATH_MAX]; struct stat st;
    if (!number_file(parent,"busnum",&bus) || !number_file(parent,"devnum",&device)
        || bus == 0 || bus > 255 || device == 0 || device > 127) return false;
    snprintf(expected,sizeof(expected),"/dev/bus/usb/%03u/%03u",bus,device);
    if (strcmp(expected,usbfs) || lstat(usbfs,&st) || !S_ISCHR(st.st_mode)
        || major(st.st_rdev)!=189 || minor(st.st_rdev)!=(bus-1)*128+device-1) return false;
    snprintf(link,sizeof(link),"/sys/dev/char/%u:%u",major(st.st_rdev),minor(st.st_rdev));
    return realpath(link,actual) && !strcmp(actual,parent);
}
static void hex(const unsigned char *data, unsigned length)
{
    putchar('"');for (unsigned i=0;i<length;i++) printf("%02x",data[i]);putchar('"');
}
int main(int argc, char **argv)
{
    if (argc!=8) { fputs("usage: control_probe TOKEN USB_PARENT EVENT HIDRAW USBFS VIRTUAL_MAC_HEX CASE\n",stderr); return 2; }
    const char *mode=argv[7];
    bool is_write=!strcmp(mode,"output-write"), is_ioctl=!strcmp(mode,"output-ioctl"),
         is_control=!strcmp(mode,"output-control"), is_feature=!strcmp(mode,"feature-status"),
         is_edge=!strcmp(mode,"feature-edge");
    if (!(is_write||is_ioctl||is_control||is_feature||is_edge)
        || !consumer_scope(argv[1],argv[2],argv[3],argv[4]) || !exact_usbfs(argv[2],argv[5])
        || strlen(argv[6])!=12 || strspn(argv[6],"0123456789abcdefABCDEF")!=12) return 2;
    const char *error=NULL; int error_number=0, returned=-1, get_returned=-1, fd=-1, usb=-1;
    unsigned char report[64]={0}, response[64]={0}, mac[20]={9}; unsigned length=48;
    struct hidraw_devinfo identity={0}; bool verified=false;
    fd=exact_open(argv[4]);
    if (fd<0) error="open attributed hidraw";
    else if (ioctl(fd,HIDIOCGRAWINFO,&identity)<0) error="HIDIOCGRAWINFO";
    else if (identity.bustype!=BUS_USB || identity.vendor!=0x054c
             || (identity.product!=0x0ce6 && identity.product!=0x0df2)) { error="virtual Sony identity";errno=EPERM; }
    else if (ioctl(fd,HIDIOCGFEATURE(sizeof(mac)),mac)!=(int)sizeof(mac)) error="pairing feature";
    else {
        char actual[13];for(unsigned i=0;i<6;i++) snprintf(actual+i*2,3,"%02x",mac[6-i]);
        if (mac[0]!=9 || mac[6]!=2 || mac[5]!=0xd5 || strcasecmp(actual,argv[6])) { error="exact virtual MAC";errno=EPERM; }
        else if (is_edge && identity.product!=0x0df2) { error="Edge persona required";errno=EINVAL; }
        else verified=true;
    }
    if (error) error_number=errno;
    if (is_feature) { length=3;report[0]=0x80;report[1]=3;report[2]=0;response[0]=0x81; }
    else if (is_edge) { length=64;report[0]=0x65;for(unsigned i=1;i<64;i++) report[i]=(unsigned char)(17+3*i);response[0]=0x65; }
    else { report[0]=2;report[2]=4;unsigned shift=is_write?0:is_ioctl?1:2;report[45]=(unsigned char)(0x45+shift*0x11);report[46]=(unsigned char)(0x67+shift*0x11);report[47]=(unsigned char)(0x89+shift*0x11); }
    uint64_t started=now_ns();
    if (verified) {
        if (is_write) returned=(int)write(fd,report,length);
        else if (is_ioctl) returned=ioctl(fd,HIDIOCSOUTPUT(length),report);
        else if (is_feature || is_edge) returned=ioctl(fd,HIDIOCSFEATURE(length),report);
        else {
            /* Kernel check_ctrlrecip may return EBUSY while usbhid owns this
             * interface. Keep that result; never detach or change recipient. */
            usb=exact_open(argv[5]);
            if (usb>=0) {
                unsigned allowed=1u<<3;
                if (ioctl(usb,USBDEVFS_DROP_PRIVILEGES,&allowed)<0) { error="drop unrelated usbfs privileges";error_number=errno; }
                else {
                    struct usbdevfs_ctrltransfer control={.bRequestType=0x21,.bRequest=9,.wValue=0x0202,
                        .wIndex=3,.wLength=48,.timeout=1000,.data=report};
                    returned=ioctl(usb,USBDEVFS_CONTROL,&control);
                }
            }
        }
        if (!error && returned<0) { error="report operation";error_number=errno; }
    }
    uint64_t ended=now_ns();
    if (verified && returned>=0 && (is_feature||is_edge)) {
        get_returned=ioctl(fd,HIDIOCGFEATURE(sizeof(response)),response);
        if (get_returned<0 && !error) { error="feature response";error_number=errno; }
    }
    uint64_t get_ended=now_ns();
    if (usb>=0) close(usb);
    if (fd>=0) close(fd);
    fputs("{\"schema\":1,\"case\":",stdout);json_string(mode);
    fputs(",\"error\":",stdout);json_string(error);
    printf(",\"errno\":%d,\"returned\":%d,\"getReturned\":%d,\"identityVerified\":%s,\"requestedBytes\":%u,\"startedMonotonicNs\":%"PRIu64",\"endedMonotonicNs\":%"PRIu64",\"getEndedMonotonicNs\":%"PRIu64,
        error_number,returned,get_returned,verified?"true":"false",length,started,ended,get_ended);
    fputs(",\"requestHex\":",stdout);hex(report,length);
    fputs(",\"responseHex\":",stdout);hex(response,get_returned>0&&get_returned<=64?(unsigned)get_returned:0);
    fputs(",\"driverDetached\":false,\"kernelPolicyModified\":false,\"physicalDeviceAccess\":false,\"latencyValidated\":false}\n",stdout);
    return error?1:0;
}
