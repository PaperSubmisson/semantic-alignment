public class ArrayIndexOutOfBoundsException2 {
  public static int func(int size) {
    if (size < 0) return -1;
    try {
      int[] a = new int[4];
      int i = a[size];
    } catch (ArrayIndexOutOfBoundsException exc) {
      return 0;
    }
    return 1;
  }
}
