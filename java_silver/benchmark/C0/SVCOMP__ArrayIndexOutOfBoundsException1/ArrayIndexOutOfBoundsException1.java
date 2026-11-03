public class ArrayIndexOutOfBoundsException1 {

  public static int func(int size) {
    if (size < 0)
      return -1;
    try {
      int[] a = new int[4];
      a[size] = 0;
    } catch (ArrayIndexOutOfBoundsException exc) {
      return 0;
    }
    return 1;
  }
  
}
